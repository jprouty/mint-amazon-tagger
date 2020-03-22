#!/usr/bin/env python3

# This script fetches Amazon "Order History Reports" and annotates your Mint
# transactions based on actual items in each purchase. It can handle orders
# that are split into multiple shipments/charges, and can even itemized each
# transaction for maximal control over categorization.

import argparse
from collections import defaultdict, Counter
import datetime
import logging
import os
import time

from progress.bar import IncrementalBar
from progress.counter import Counter as ProgressCounter
from progress.spinner import Spinner
from outdated import check_outdated

from mintamazontagger import amazon
from mintamazontagger import mint
from mintamazontagger import tagger
from mintamazontagger import VERSION
from mintamazontagger.args import define_cli_args
from mintamazontagger.asyncprogress import AsyncProgress
from mintamazontagger.currency import micro_usd_to_usd_string
from mintamazontagger.mint import (
    get_trans_and_categories_from_pickle, dump_trans_and_categories)
from mintamazontagger.mintclient import MintClient
from mintamazontagger.orderhistory import fetch_order_history

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def main():
    root_logger = logging.getLogger()
    root_logger.addHandler(logging.StreamHandler())
    # For helping remote debugging, also log to file.
    # Developers should be vigilant to NOT log any PII, ever (including being
    # mindful of what exceptions might be thrown).
    home = os.path.expanduser("~")
    log_directory = os.path.join(home, 'Tagger Logs')
    os.makedirs(log_directory, exist_ok=True)
    log_filename = os.path.join(log_directory, '{}.log'.format(
        time.strftime("%Y-%m-%d_%H-%M-%S")))
    root_logger.addHandler(logging.FileHandler(log_filename))

    is_outdated, latest_version = check_outdated('mint-amazon-tagger', VERSION)
    if is_outdated:
        print('Please update your version by running:\n'
              'pip3 install mint-amazon-tagger --upgrade\n\n')

    parser = argparse.ArgumentParser(
        description='Tag Mint transactions based on itemized Amazon history.')
    define_cli_args(parser)
    args = parser.parse_args()

    if args.version:
        print('mint-amazon-tagger {}\nBy: Jeff Prouty'.format(VERSION))
        exit(0)

    items_csv = args.items_csv
    orders_csv = args.orders_csv
    refunds_csv = args.refunds_csv

    start_date = None
    if not items_csv or not orders_csv:
        logger.info('Missing Items/Orders History csv. Attempting to fetch '
                    'from Amazon.com.')
        start_date = args.order_history_start_date
        end_date = args.order_history_end_date

        items_csv, orders_csv, refunds_csv = fetch_order_history(
            args.report_download_location, start_date, end_date,
            args.amazon_email, args.amazon_password,
            args.session_path, args.headless,
            progress_factory=lambda msg, max: AsyncProgress(Spinner(msg)))

    if not items_csv or not orders_csv:  # Refunds are optional
        logger.critical('Order history either not provided at command line or '
                        'unable to fetch. Exiting.')
        exit(1)

    orders = amazon.Order.parse_from_csv(
        orders_csv, ProgressCounter('Parsing Orders - '))
    items = amazon.Item.parse_from_csv(
        items_csv, ProgressCounter('Parsing Items - '))
    refunds = ([] if not refunds_csv
               else amazon.Refund.parse_from_csv(
                   refunds_csv, ProgressCounter('Parsing Refunds - ')))

    if not len(orders):
        logger.critical('The Orders report contains no data. Try '
                        'downloading again. Report used: {}'.format(
                            orders_csv))
        exit(1)
    if not len(items):
        logger.critical('The Items report contains no data. Try '
                        'downloading again. Report used: {}'.format(
                            items_csv))
        exit(1)
    if refunds_csv and not len(refunds):
        logger.warning('No Refunds found, despite having a Refunds '
                       'Report given.')

    if args.dry_run:
        logger.info('\nDry Run; no modifications being sent to Mint.\n')

    # Initialize the stats. Explicitly initialize stats that might not be
    # accumulated (conditionals).
    stats = Counter(
        adjust_itemized_tax=0,
        already_up_to_date=0,
        misc_charge=0,
        new_tag=0,
        no_retag=0,
        retag=0,
        user_skipped_retag=0,
        personal_cat=0,
    )

    mint_client = MintClient(
            email=args.mint_email,
            password=args.mint_password,
            session_path=args.session_path,
            headless=args.headless,
            mfa_method=args.mint_mfa_method,
            wait_for_sync=args.mint_wait_for_sync,
            progress_factory=lambda msg, max: AsyncProgress(Spinner(msg)))
    if args.pickled_epoch:
        label = 'Un-pickling Mint transactions from epoch: {} '.format(
            args.pickled_epoch)
        asyncSpin = AsyncProgress(Spinner(label))
        mint_trans, mint_category_name_to_id = (
            get_trans_and_categories_from_pickle(
                args.pickled_epoch, args.mint_pickle_location))
        asyncSpin.finish()
    else:
        # Get the date of the oldest Amazon order.
        if not start_date:
            start_date = min([o.order_date for o in orders])
            if refunds:
                start_date = min(
                    start_date,
                    min([o.order_date for o in refunds]))

        # Double the length of transaction history to help aid in
        # personalized category tagging overrides.
        # TODO: Revise this logic/date range.
        today = datetime.date.today()
        start_date = today - (today - start_date) * 2

        # HACK: Work around the nested progress by initializing the mint
        # connection here.
        mint_client.get_mintapi()
        asyncSpin = AsyncProgress(Spinner('Fetching Categories '))
        mint_category_name_to_id = mint_client.get_categories()
        asyncSpin.finish()

        asyncSpin = AsyncProgress(Spinner('Fetching Transactions '))
        mint_transactions_json = mint_client.get_transactions(start_date)
        mint_trans = mint.Transaction.parse_from_json(mint_transactions_json)
        asyncSpin.finish()

        if args.save_pickle_backup:
            pickle_epoch = int(time.time())
            label = 'Backing up Mint to local pickle file, epoch: {} '.format(
                pickle_epoch)
            asyncSpin = AsyncProgress(Spinner(label))
            dump_trans_and_categories(
                mint_trans, mint_category_name_to_id, pickle_epoch,
                args.mint_pickle_location)
            asyncSpin.finish()

    updates, unmatched_orders = tagger.get_mint_updates(
        orders, items, refunds,
        mint_trans,
        args, stats,
        mint_category_name_to_id,
        progress_factory=lambda msg, max: IncrementalBar(
            msg, max=max))

    log_amazon_stats(items, orders, refunds)
    log_processing_stats(stats)

    if args.print_unmatched and unmatched_orders:
        logger.warning(
            'The following were not matched to Mint transactions:\n')
        by_oid = defaultdict(list)
        for uo in unmatched_orders:
            by_oid[uo.order_id].append(uo)
        for unmatched_by_oid in by_oid.values():
            orders = [o for o in unmatched_by_oid if o.is_debit]
            refunds = [o for o in unmatched_by_oid if not o.is_debit]
            if orders:
                print_unmatched(amazon.Order.merge(orders))
            for r in amazon.Refund.merge(refunds):
                print_unmatched(r)

    if not updates:
        logger.info(
            'All done; no new tags to be updated at this point in time!')
        exit(0)

    if args.dry_run:
        logger.info('Dry run. Following are proposed changes:')
        if args.skip_dry_print:
            logger.info('Dry run print results skipped!')
        else:
            tagger.print_dry_run(updates,
                                 ignore_category=args.no_tag_categories)

    else:
        num_updates = mint_client.send_updates(
            updates,
            progress=IncrementalBar(
                'Updating Mint',
                max=len(updates)),
            ignore_category=args.no_tag_categories)

        logger.info('Sent {} updates to Mint'.format(num_updates))


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
        if amzn_obj.is_debit else [amzn_obj.get_title()],
        '{}{}: '.format(
            amzn_obj.website, '' if amzn_obj.is_debit else ' refund'))
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
