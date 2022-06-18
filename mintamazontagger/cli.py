#!/usr/bin/env python3

# This script fetches Amazon "Order History Reports" and annotates your Mint
# transactions based on actual items in each purchase. It can handle orders
# that are split into multiple shipments/charges, and can even itemized each
# transaction for maximal control over categorization.

import argparse
import atexit
from collections import defaultdict
import getpass
import logging
import os
from signal import signal, SIGINT
import time

from outdated import check_outdated

from mintamazontagger import amazon
from mintamazontagger import mint
from mintamazontagger import tagger
from mintamazontagger import VERSION
from mintamazontagger.args import (
    define_cli_args, has_order_history_csv_files, TAGGER_BASE_PATH)
from mintamazontagger.my_progress import (
    counter_progress_cli, determinate_progress_cli, indeterminate_progress_cli)
from mintamazontagger.currency import micro_usd_to_usd_string
from mintamazontagger.mintclient import MintClient
from mintamazontagger.orderhistory import fetch_order_history
from mintamazontagger.webdriver import get_webdriver

logger = logging.getLogger(__name__)


def main():
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(logging.StreamHandler())
    # Disable noisy log spam from filelock from within tldextract.
    logging.getLogger("filelock").setLevel(logging.WARN)

    # For helping remote debugging, also log to file.
    # Developers should be vigilant to NOT log any PII, ever (including being
    # mindful of what exceptions might be thrown).
    log_directory = os.path.join(TAGGER_BASE_PATH, 'Tagger Logs')
    os.makedirs(log_directory, exist_ok=True)
    log_filename = os.path.join(log_directory, '{}.log'.format(
        time.strftime("%Y-%m-%d_%H-%M-%S")))
    root_logger.addHandler(logging.FileHandler(log_filename))

    logger.info('Running version {}'.format(VERSION))
    try:
        is_outdated, latest_version = check_outdated(
            'mint-amazon-tagger', VERSION)
        if is_outdated:
            logger.warning('Please update your version by running:\n'
                           'pip3 install mint-amazon-tagger --upgrade\n\n')
    except ValueError:
        logger.error(
            'Version {} is newer than PyPY version'.format(VERSION))

    parser = argparse.ArgumentParser(
        description='Tag Mint transactions based on itemized Amazon history.')
    define_cli_args(parser)
    args = parser.parse_args()

    if args.version:
        print('mint-amazon-tagger {}\nBy: Jeff Prouty'.format(VERSION))
        exit(0)

    webdriver = None

    def close_webdriver():
        if webdriver:
            webdriver.close()

    atexit.register(close_webdriver)

    def webdriver_factory():
        nonlocal webdriver
        if webdriver:
            return webdriver
        webdriver = get_webdriver(args.headless, args.session_path)
        return webdriver

    def sigint_handler(signal, frame):
        if webdriver:
            webdriver.close()
        logger.warning('Keyboard interrupt caught')
        exit(0)

    signal(SIGINT, sigint_handler)

    mint_client = MintClient(args, webdriver_factory)

    # Attempt to fetch the order history if csv files are not already provided.
    if not has_order_history_csv_files(args):
        if not maybe_prompt_for_amazon_credentials(args):
            logger.critical('Failed to get Amazon credentials.')
            exit(1)
        if not fetch_order_history(
                args, webdriver_factory, indeterminate_progress_cli):
            logger.critical('Failed to fetch Amazon order history.')
            exit(1)

    if args.dry_run:
        logger.info('\nDry Run; no modifications being sent to Mint.\n')

    def on_critical(msg):
        logger.critical(msg)
        exit(1)

    maybe_prompt_for_mint_credentials(args)
    results = tagger.create_updates(
        args, mint_client,
        on_critical=on_critical,
        indeterminate_progress_factory=indeterminate_progress_cli,
        determinate_progress_factory=determinate_progress_cli,
        counter_progress_factory=counter_progress_cli)

    if not results.success:
        logger.critical('Uncaught error from create_updates. Exiting')
        exit(1)

    log_amazon_stats(results.items, results.orders, results.refunds)
    log_processing_stats(results.stats)

    if args.print_unmatched and results.unmatched_orders:
        logger.warning(
            'The following were not matched to Mint transactions:\n')
        by_oid = defaultdict(list)
        for uo in results.unmatched_orders:
            by_oid[uo.order_id].append(uo)
        for unmatched_by_oid in by_oid.values():
            orders = [o for o in unmatched_by_oid if not o.is_refund]
            refunds = [o for o in unmatched_by_oid if o.is_refund]
            if orders:
                print_unmatched(amazon.Order.merge(orders))
            for r in amazon.Refund.merge(refunds):
                print_unmatched(r)

    if not results.updates:
        logger.info(
            'All done; no new tags to be updated at this point in time!')
        exit(0)

    if args.dry_run:
        logger.info('Dry run. Following are proposed changes:')
        if args.skip_dry_print:
            logger.info('Dry run print results skipped!')
        else:
            tagger.print_dry_run(results.updates,
                                 ignore_category=args.no_tag_categories)
    else:
        num_updates = mint_client.send_updates(
            results.updates,
            progress=determinate_progress_cli(
                'Updating Mint',
                max=len(results.updates)),
            ignore_category=args.no_tag_categories)

        logger.info('Sent {} updates to Mint'.format(num_updates))


def maybe_prompt_for_mint_credentials(args):
    if (not args.mint_email and not args.mint_user_will_login
            and not args.pickled_epoch):
        args.mint_email = input('Mint email: ')
    if (not args.mint_password and not args.mint_user_will_login
            and not args.pickled_epoch):
        args.mint_password = getpass.getpass('Mint password: ')


def maybe_prompt_for_amazon_credentials(args):
    if not args.amazon_email and not args.amazon_user_will_login:
        args.amazon_email = input('Amazon email: ')
    if not args.amazon_email and not args.amazon_user_will_login:
        logger.error('Empty Amazon email.')
        return False

    if not args.amazon_password and not args.amazon_user_will_login:
        args.amazon_password = getpass.getpass('Amazon password: ')
    if not args.amazon_password and not args.amazon_user_will_login:
        logger.error('Empty Amazon password.')
        return False
    return True


def log_amazon_stats(items, orders, refunds):
    logger.info('\nAmazon Stats:')
    if len(orders) == 0 or len(items) == 0:
        logger.info('\tThere were not Amazon orders/items!')
        return
    logger.info('\n{} orders with {} matching items'.format(
        len([o for o in orders if o.items_matched]),
        len([i for i in items if i.matched])))
    logger.info('{} unmatched orders and {} unmatched items'.format(
        len([o for o in orders if not o.items_matched]),
        len([i for i in items if not i.matched])))

    first_order_date = min([o.order_date for o in orders])
    last_order_date = max([o.order_date for o in orders])
    logger.info('Orders ranging from {} to {}'.format(
        first_order_date, last_order_date))

    per_item_totals = [i.item_total for i in items]
    per_order_totals = [o.total_charged for o in orders]

    logger.info('{} total spend'.format(
        micro_usd_to_usd_string(sum(per_order_totals))))

    logger.info('{} avg order total (range: {} - {})'.format(
        micro_usd_to_usd_string(sum(per_order_totals) / len(orders)),
        micro_usd_to_usd_string(min(per_order_totals)),
        micro_usd_to_usd_string(max(per_order_totals))))
    logger.info('{} avg item price (range: {} - {})'.format(
        micro_usd_to_usd_string(sum(per_item_totals) / len(items)),
        micro_usd_to_usd_string(min(per_item_totals)),
        micro_usd_to_usd_string(max(per_item_totals))))

    if refunds:
        first_refund_date = min(
            [r.refund_date for r in refunds if r.refund_date])
        last_refund_date = max(
            [r.refund_date for r in refunds if r.refund_date])
        logger.info('\n{} refunds dating from {} to {}'.format(
            len(refunds), first_refund_date, last_refund_date))

        per_refund_totals = [r.total_refund_amount for r in refunds]

        logger.info('{} total refunded'.format(
            micro_usd_to_usd_string(sum(per_refund_totals))))


def log_processing_stats(stats):
    logger.info(
        '\nTransactions: {trans}\n'
        'Transactions w/ "Amazon" in description: {amazon_in_desc}\n'
        'Transactions ignored: is pending: {pending}\n'
        '\n'
        'Orders matched w/ transactions: {order_match} (unmatched orders: '
        '{order_unmatch})\n'
        'Refunds matched w/ transactions: {refund_match} (unmatched refunds: '
        '{refund_unmatch})\n'
        'Transactions matched w/ orders/refunds: {trans_match} (unmatched: '
        '{trans_unmatch})\n'
        '\n'
        'Orders skipped: not shipped: {skipped_orders_unshipped}\n'
        'Orders skipped: gift card used: {skipped_orders_gift_card}\n'
        '\n'
        'Order fix-up: incorrect tax itemization: {adjust_itemized_tax}\n'
        'Order fix-up: has a misc charges (e.g. gift wrap): {misc_charge}\n'
        '\n'
        'Transactions ignored; already tagged & up to date: '
        '{already_up_to_date}\n'
        'Transactions ignored; ignore retags: {no_retag}\n'
        'Transactions ignored; user skipped retag: {user_skipped_retag}\n'
        '\n'
        'Transactions with personalize categories: {personal_cat}\n'
        '\n'
        'Transactions to be retagged: {retag}\n'
        'Transactions to be newly tagged: {new_tag}\n'.format(**stats))


def print_unmatched(amzn_obj):
    proposed_mint_desc = mint.summarize_title(
        [i.get_title() for i in amzn_obj.items]
        if not amzn_obj.is_refund else [amzn_obj.get_title()],
        '{}{}: '.format(
            amzn_obj.website, '' if not amzn_obj.is_refund else ' refund'))
    logger.warning('{}'.format(proposed_mint_desc))
    logger.warning('\t{}\t{}\t{}'.format(
        amzn_obj.transact_date()
        if amzn_obj.transact_date()
        else 'Never shipped!',
        micro_usd_to_usd_string(amzn_obj.transact_amount()),
        amazon.get_invoice_url(amzn_obj.order_id)))
    logger.warning('')


if __name__ == '__main__':
    main()
