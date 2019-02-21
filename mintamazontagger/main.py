#!/usr/bin/env python3

# This script fetches Amazon "Order History Reports" and annotates your Mint
# transactions based on actual items in each purchase. It can handle orders
# that are split into multiple shipments/charges, and can even itemized each
# transaction for maximal control over categorization.

import argparse
from collections import defaultdict, Counter
import datetime
import logging
import pickle
import os
import time

from progress.counter import Counter as ProgressCounter
from progress.spinner import Spinner
from outdated import warn_if_outdated

from mintamazontagger import amazon
from mintamazontagger import mint
from mintamazontagger import tagger
from mintamazontagger import VERSION
from mintamazontagger.asyncprogress import AsyncProgress
from mintamazontagger.currency import micro_usd_to_usd_string
from mintamazontagger.orderhistory import fetch_order_history
from mintamazontagger.mintclient import MintClient

logger = logging.getLogger(__name__)
logger.addHandler(logging.StreamHandler())
logger.setLevel(logging.INFO)


def main():
    warn_if_outdated('mint-amazon-tagger', VERSION)

    parser = argparse.ArgumentParser(
        description='Tag Mint transactions based on itemized Amazon history.')
    define_args(parser)
    args = parser.parse_args()

    if args.version:
        print('mint-amazon-tagger {}\nBy: Jeff Prouty'.format(VERSION))
        exit(0)

    session_path = args.session_path
    if session_path.lower() == 'none':
        session_path = None

    items_csv = args.items_csv
    orders_csv = args.orders_csv
    refunds_csv = args.refunds_csv

    start_date = None
    if not items_csv or not orders_csv:
        logger.info('Missing Items/Orders History csv. Attempting to fetch '
                    'from Amazon.com.')
        start_date = args.order_history_start_date
        duration = datetime.timedelta(days=args.order_history_num_days)
        end_date = datetime.date.today()
        # If a start date is given, adjust the end date based on num_days,
        # ensuring not to go beyond today.
        if start_date:
            start_date = start_date.date()
            if start_date + duration < end_date:
                end_date = start_date + duration
        else:
            start_date = end_date - duration
        items_csv, orders_csv, refunds_csv = fetch_order_history(
            args.report_download_location, start_date, end_date,
            args.amazon_email, args.amazon_password,
            session_path, args.headless)

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

    mint_client = MintClient(args.mint_email, args.mint_password,
                             session_path, args.headless,
                             args.mint_mfa_method)

    if args.pickled_epoch:
        mint_trans, mint_category_name_to_id = (
            get_trans_and_categories_from_pickle(
                args.pickled_epoch, args.mint_pickle_location))
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
        today = datetime.date.today()
        start_date = today - (today - start_date) * 2
        mint_category_name_to_id = mint_client.get_categories()
        mint_transactions_json = mint_client.get_transactions(start_date)

        epoch = int(time.time())
        mint_trans = mint.Transaction.parse_from_json(mint_transactions_json)
        dump_trans_and_categories(
            mint_trans, mint_category_name_to_id, epoch,
            args.mint_pickle_location)

    updates, unmatched_orders = tagger.get_mint_updates(
        orders, items, refunds,
        mint_trans,
        args, stats,
        mint_category_name_to_id)

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
        mint_client.send_updates(
            updates, ignore_category=args.no_tag_categories)


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


MINT_TRANS_PICKLE_FMT = 'Mint {} Transactions.pickle'
MINT_CATS_PICKLE_FMT = 'Mint {} Categories.pickle'


def get_trans_and_categories_from_pickle(pickle_epoch, pickle_base_path):
    label = 'Un-pickling Mint transactions from epoch: {} '.format(
        pickle_epoch)
    asyncSpin = AsyncProgress(Spinner(label))
    trans_pickle_path = os.path.join(
        pickle_base_path, MINT_TRANS_PICKLE_FMT.format(pickle_epoch))
    cats_pickle_path = os.path.join(
        pickle_base_path, MINT_CATS_PICKLE_FMT.format(pickle_epoch))
    with open(trans_pickle_path, 'rb', encoding='utf-8') as f:
        trans = pickle.load(f)
    with open(cats_pickle_path, 'rb', encoding='utf-8') as f:
        cats = pickle.load(f)
    asyncSpin.finish()

    return trans, cats


def dump_trans_and_categories(trans, cats, pickle_epoch, pickle_base_path):
    label = 'Backing up Mint to local pickle file, epoch: {} '.format(
        pickle_epoch)
    asyncSpin = AsyncProgress(Spinner(label))
    if not os.path.exists(pickle_base_path):
        os.makedirs(pickle_base_path)
    trans_pickle_path = os.path.join(
        pickle_base_path, MINT_TRANS_PICKLE_FMT.format(pickle_epoch))
    cats_pickle_path = os.path.join(
        pickle_base_path, MINT_CATS_PICKLE_FMT.format(pickle_epoch))
    with open(trans_pickle_path, 'wb', encoding='utf-8') as f:
        pickle.dump(trans, f)
    with open(cats_pickle_path, 'wb', encoding='utf-8') as f:
        pickle.dump(cats, f)
    asyncSpin.finish()


def define_args(parser):
    # Amazon creds:
    parser.add_argument(
        '--amazon_email', default=None,
        help=('Amazon e-mail. If not provided, you will be '
              'prompted for it.'))
    parser.add_argument(
        '--amazon_password', default=None,
        help=('Amazon password. If not provided, you will be '
              'prompted for it.'))

    # History options"
    parser.add_argument(
        '--order_history_location', type=str,
        default="AMZN Reports",
        help='Where to store the fetched Amazon "order history" reports.')
    parser.add_argument(
        '--order_history_num_days', type=int,
        default=90,
        help='How many days of order history to retrieve. Default: 90 days')
    parser.add_argument(
        '--order_history_start_date',
        type=lambda s: datetime.datetime.strptime(s, '%Y-%m-%d'),
        default=None,
        help=('If None, start_date is num_days ago from today. '
              'If given, this is the start_date, with the end date being '
              'start_date + num_days. Format: YYYY-MM-DD'))
    parser.add_argument(
        '--report_download_location', type=str,
        default='AMZN Reports',
        help='Where to place the downloaded reports.')

    # Amazon Input, as CSV file:
    parser.add_argument(
        '--items_csv', type=argparse.FileType('r'),
        help=('The "Items" Order History Report from Amazon. If not present, '
              'will try to fetch order history for you. See --amazon_email.'))
    parser.add_argument(
        '--orders_csv', type=argparse.FileType('r'),
        help='The "Orders and Shipments" Order History Report from Amazon')
    parser.add_argument(
        '--refunds_csv', type=argparse.FileType('r'),
        help='The "Refunds" Order History Report from Amazon. '
             'This is optional.')

    # Mint creds:
    parser.add_argument(
        '--mint_email', default=None,
        help=('Mint e-mail address for login. If not provided here, will be '
              'prompted for user.'))
    parser.add_argument(
        '--mint_password', default=None,
        help=('Mint password for login. If not provided here, will be '
              'prompted for.'))
    parser.add_argument(
        '--mint_mfa_method',
        default='sms',
        choices=['sms', 'email'],
        help='The Mint MFA method (2factor auth codes).')

    # To itemize or not to itemize; that is the question:
    parser.add_argument(
        '--verbose_itemize', action='store_true',
        help=('Default behavior is to not itemize out shipping/promos/etc if '
              'there is only one item per Mint transaction. Will also remove '
              'free shipping. Set this to itemize everything.'))
    parser.add_argument(
        '--no_itemize', action='store_true',
        help=('Do not split Mint transactions into individual items with '
              'attempted categorization.'))

    # Debugging/testing.
    parser.add_argument(
        '--pickled_epoch', type=int,
        help=('Do not fetch categories or transactions from Mint. Use this '
              'pickled epoch instead. If coupled with --dry_run, no '
              'connection to Mint is established.'))
    parser.add_argument(
        '--mint_pickle_location', type=str,
        default="Mint Backup",
        help='Where to store the fetched Mint pickles (for backup).')
    parser.add_argument(
        '--dry_run', action='store_true',
        help=('Do not modify Mint transaction; instead print the proposed '
              'changes to console.'))
    parser.add_argument(
        '--skip_dry_print', action='store_true',
        help=('Do not print dry run results (useful for development).'))
    parser.add_argument(
        '--num_updates', type=int,
        default=0,
        help=('Only send the first N updates to Mint (or print N updates at '
              'dry run). If not present, all updates are sent or printed.'))
    parser.add_argument(
        '-V', '--version', action='store_true',
        help='Shows the app version and quits.')

    # Retag transactions that have already been tagged previously:
    parser.add_argument(
        '--prompt_retag', action='store_true',
        help=('For transactions that have been previously tagged by this '
              'script, override any edits (like adjusting the category) but '
              'only after confirming each change. More gentle than '
              '--retag_changed'))
    parser.add_argument(
        '--retag_changed', action='store_true',
        help=('For transactions that have been previously tagged by this '
              'script, override any edits (like adjusting the category). This '
              'feature works by looking for "Amazon.com: " at the start of a '
              'transaction. If the user changes the description, then the '
              'tagger won\'t know to leave it alone.'))
    parser.add_argument(
        '--print_unmatched', action='store_true',
        help=('At completion, print unmatched orders to help manual tagging.'))

    # Prefix customization:
    parser.add_argument(
        '--description_prefix_override', type=str,
        help=('The prefix to use when updating the description for each Mint '
              'transaction. By default, the \'Website\' value from Amazon '
              'Items/Orders csv is used. If a string is provided, use '
              'this instead for all matched transactions. If given, this is '
              'used in conjunction with amazon_domains to detect if a '
              'transaction has already been tagged by this tool.'))
    parser.add_argument(
        '--description_return_prefix_override', type=str,
        help=('The prefix to use when updating the description for each Mint '
              'refund. By default, the \'Website\' value from Amazon '
              'Items/Orders csv is used with refund appended (e.g. '
              '\'Amazon.com Refund: ...\'. If a string is provided here, use '
              'this instead for all matched refunds. If given, this is '
              'used in conjunction with amazon_domains to detect if a '
              'refund has already been tagged by this tool.'))
    parser.add_argument(
        '--amazon_domains', type=str,
        # From: https://en.wikipedia.org/wiki/Amazon_(company)#Website
        default=('amazon.com,amazon.cn,amazon.in,amazon.co.jp,amazon.com.sg,'
                 'amazon.com.tr,amazon.fr,amazon.de,amazon.it,amazon.nl,'
                 'amazon.es,amazon.co.uk,amazon.ca,amazon.com.mx,'
                 'amazon.com.au,amazon.com.br'),
        help=('A list of all valid Amazon domains/websites. These should '
              'match the website column from Items/Orders and is used to '
              'detect if a transaction has already been tagged by this tool.'))

    parser.add_argument(
        '--mint_input_merchant_filter', type=str,
        default='amazon,amzn',
        help=('Only consider Mint transactions that have one of these strings '
              'in the merchant field. Case-insensitive comma-separated.'))
    parser.add_argument(
        '--mint_input_categories_filter', type=str,
        help=('If present, only consider Mint transactions that match one of '
              'the given categories here. Comma separated list of Mint '
              'categories.'))

    # Tagging options:
    parser.add_argument(
        '--no_tag_categories', action='store_true',
        help=('If present, do not update Mint categories. This is useful as '
              'Amazon doesn\'t provide the best categorization and it is '
              'pretty common user behavior to manually change the categories. '
              'This flag prevents tagger from wiping out that user work.'))
    parser.add_argument(
        '--do_not_predict_categories', action='store_true',
        help=('Do not attempt to predict custom category tagging based on any '
              'tagging overrides. By default (no arg) tagger will attempt to '
              'find items that you have manually changed categories for.'))

    # Mint API options:
    home = os.path.expanduser("~")
    default_session_path = os.path.join(home, '.mintapi', 'session')
    parser.add_argument(
        '--session-path', nargs='?',
        default=default_session_path,
        help=('Directory to save browser session, including cookies. Use to '
              'prevent repeated MFA prompts. Defaults to ~/.mintapi/session. '
              'Set to None to use a temporary profile.'))
    parser.add_argument(
        '--headless',
        action='store_true',
        help='Whether to execute chromedriver with no visible window.')


if __name__ == '__main__':
    main()
