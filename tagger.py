#!/usr/bin/env python3

# This script takes Amazon "Order History Reports" and annotates your Mint
# transactions based on actual items in each purchase. It can handle orders
# that are split into multiple shipments/charges, and can even itemized each
# transaction for maximal control over categorization.

# First, you must generate and download your order history reports from:
# https://www.amazon.com/gp/b2b/reports

import argparse
import atexit
from collections import defaultdict, Counter
import itertools
import logging
import pickle
from pprint import pprint
import time

import getpass
import keyring
# Temporary until mintapi is fixed upstream.
from mintapifuture.mintapi.api import Mint, MINT_ROOT_URL
import readchar

import amazon
import category
import mint


logger = logging.getLogger(__name__)
logger.addHandler(logging.StreamHandler())
logger.setLevel(logging.INFO)


DEFAULT_MERCHANT_PREFIX = 'Amazon.com: '
DEFAULT_MERCHANT_REFUND_PREFIX = 'Amazon.com refund: '

KEYRING_SERVICE_NAME = 'mintapi'

UPDATE_TRANS_ENDPOINT = '/updateTransaction.xevent'


def main():
    parser = argparse.ArgumentParser(
        description='Tag Mint transactions based on itemized Amazon history.')
    define_args(parser)
    args = parser.parse_args()

    if args.dry_run:
        logger.info('Dry Run; no modifications being sent to Mint.')

    orders = amazon.Order.parse_from_csv(args.orders_csv)  
    items = amazon.Item.parse_from_csv(args.items_csv)
    logger.info('Matching Amazon Items with Orders')
    amazon.associate_items_with_orders(orders, items)

    # Only keep orders that have items.
    orders = [o for o in orders if o.items]

    refunds = [] if not args.refunds_csv else amazon.Refund.parse_from_csv(args.refunds_csv)

    mint_client = None

    def close_mint_client():
        if mint_client:
            mint_client.close()

    atexit.register(close_mint_client)

    if args.pickled_epoch:
        mint_transactions_json, mint_category_name_to_id = (
            get_trans_and_categories_from_pickle(args.pickled_epoch))
    else:
        mint_client = get_mint_client(args)

        # Only get transactions as new as the oldest Amazon order.
        oldest_trans_date = min([o.order_date for o in orders])
        if refunds:
            oldest_trans_date = min(
                oldest_trans_date,
                min([o.order_date for o in refunds]))
        mint_transactions_json, mint_category_name_to_id = (
            get_trans_and_categories_from_mint(mint_client, oldest_trans_date))
        epoch = int(time.time())
        dump_trans_and_categories(
            mint_transactions_json, mint_category_name_to_id, epoch)

    def get_prefix(is_debit):
        return (args.description_prefix if is_debit
                else args.description_return_prefix)

    trans = mint.Transaction.parse_from_json(mint_transactions_json)
    trans = mint.Transaction.unsplit(trans)
    # Skip t if the original description doesn't contain 'amazon'
    trans = [t for t in trans if 'amazon' in t.omerchant.lower()]
    # Skip t if it's pending.
    trans = [t for t in trans if not t.is_pending]

    # Match orders.
    match_transactions(trans, orders)

    unmatched_trans = [t for t in trans if not t.orders]
        
    # Match refunds.
    match_transactions(unmatched_trans, refunds)

    unmatched_orders = [o for o in orders if not o.matched]
    unmatched_trans = [t for t in trans if not t.orders]
    unmatched_refunds = [r for r in refunds if not r.matched]

    num_gift_card = len([o for o in unmatched_orders
                         if 'Gift Certificate' in o.payment_instrument_type])

    matched_orders = [o for o in orders if o.matched]
    matched_trans = [t for t in trans if t.orders]
    matched_refunds = [r for r in refunds if r.matched]

    merged_orders = []
    merged_refunds = []
    
    # Collapse per-item into quantities so it presents nicely.
    for t in matched_trans:
        if t.is_debit:
            t.orders = amazon.Order.merge_orders(t.orders)
            t.orders[0].fix_itemized_tax()
            merged_orders.extend(t.orders)
        else:
            t.orders = amazon.Refund.merge_refunds(t.orders)
            merged_refunds.extend(t.orders)

        new_transactions = t.orders[0].to_mint_transactions(t)

    
def mark_best_as_matched(t, list_of_orders_or_refunds):
    if not list_of_orders_or_refunds:
        return

    # Only consider it a match if the posted date (transaction date) is
    # within 3 days of the ship date of the order.
    closest_match = None
    closest_match_num_days = 365  # Large number
    for orders in list_of_orders_or_refunds:
        an_order = next(o for o in orders if o.transact_date())
        if not an_order:
            continue
        num_days = (t.odate - an_order.transact_date()).days
        # TODO: consider orders even if it has a matched_transaction if this
        # transaction is closer.
        already_matched = any([o.matched for o in orders])
        if (abs(num_days) < 4 and
                abs(num_days) < closest_match_num_days and
                not already_matched):
            closest_match = orders
            closest_match_num_days = abs(num_days)

    if closest_match:
        for o in closest_match:
            o.match(t)
        t.match(closest_match)


def match_transactions(unmatched_trans, unmatched_orders):
    # Also works with Refund objects.
    # First pass: Match up transactions that exactly equal an order's charged
    # amount.
    amount_to_orders = defaultdict(list)
    for o in unmatched_orders:
        amount_to_orders[o.transact_amount()].append([o])

    for t in unmatched_trans:
        mark_best_as_matched(t, amount_to_orders[t.amount])

    unmatched_orders =  [o for o in unmatched_orders if not o.matched]
    unmatched_trans = [t for t in unmatched_trans if not t.orders]

    # Second pass: Match up transactions to a combination of orders (sometimes
    # they are charged together).
    oid_to_orders = defaultdict(list)
    for o in unmatched_orders:
        oid_to_orders[o.order_id].append(o)
    amount_to_orders = defaultdict(list)
    for orders_same_id in oid_to_orders.values():
        combos = []
        for r in range(2, len(orders_same_id) + 1):
            combos.extend(itertools.combinations(orders_same_id, r))
        for c in combos:
            orders_total = sum([o.transact_amount() for o in c])
            amount_to_orders[orders_total].append(c)

    for t in unmatched_trans:
        mark_best_as_matched(t, amount_to_orders[t.amount])

    
def get_mint_client(args):
    email = args.mint_email
    password = args.mint_password

    if not email:
        email = input('Mint email: ')

    if not password:
        password = keyring.get_password(KEYRING_SERVICE_NAME, email)

    if not password:
        password = getpass.getpass('Mint password: ')

    if not email or not password:
        logger.error('Missing Mint email or password.')
        exit(1)

    logger.info('Logging in via chromedriver')
    mint_client = Mint.create(email, password)

    logger.info('Login successful!')

    # On success, save off password to keyring.
    keyring.set_password(KEYRING_SERVICE_NAME, email, password)

    return mint_client


MINT_TRANS_PICKLE_FMT = 'Mint {} Transactions.pickle'
MINT_CATS_PICKLE_FMT = 'Mint {} Categories.pickle'


def get_trans_and_categories_from_pickle(pickle_epoch):
    logger.info('Restoring from pickle backup epoch: {}.'.format(
        pickle_epoch))
    with open(MINT_TRANS_PICKLE_FMT.format(pickle_epoch), 'rb') as f:
        trans = pickle.load(f)
    with open(MINT_CATS_PICKLE_FMT.format(pickle_epoch), 'rb') as f:
        cats = pickle.load(f)

    return trans, cats


def dump_trans_and_categories(trans, cats, pickle_epoch):
    logger.info(
        'Backing up Mint Transactions prior to editing. '
        'Pickle epoch: {}'.format(pickle_epoch))
    with open(MINT_TRANS_PICKLE_FMT.format(pickle_epoch), 'wb') as f:
        pickle.dump(trans, f)
    with open(MINT_CATS_PICKLE_FMT.format(pickle_epoch), 'wb') as f:
        pickle.dump(cats, f)


def get_trans_and_categories_from_mint(mint_client, oldest_trans_date):
    # Create a map of Mint category name to category id.
    logger.info('Creating Mint Category Map.')
    categories = dict([
        (cat_dict['name'], cat_id)
        for (cat_id, cat_dict) in mint_client.get_categories().items()])

    start_date_str = oldest_trans_date.strftime('%m/%d/%y')
    logger.info('Fetching all Mint transactions since {}.'.format(
        start_date_str))
    transactions = mint_client.get_transactions_json(
        start_date=start_date_str,
        include_investment=False,
        skip_duplicates=True)

    return transactions, categories


def define_args(parser):
    parser.add_argument(
        '--mint_email', default=None,
        help=('Mint e-mail address for login. If not provided here, will be '
              'prompted for user.'))
    parser.add_argument(
        '--mint_password', default=None,
        help=('Mint password for login. If not provided here, will be '
              'prompted for.'))

    parser.add_argument(
        'items_csv', type=argparse.FileType('r'),
        help='The "Items" Order History Report from Amazon')
    parser.add_argument(
        'orders_csv', type=argparse.FileType('r'),
        help='The "Orders and Shipments" Order History Report from Amazon')
    parser.add_argument(
        '--refunds_csv', type=argparse.FileType('r'),
        help='The "Refunds" Order History Report from Amazon. '
             'This is optional.')

    parser.add_argument(
        '--no_itemize', action='store_true',
        help=('P will split Mint transactions into individual items with '
              'attempted categorization.'))

    parser.add_argument(
        '--pickled_epoch', type=int,
        help=('Do not fetch categories or transactions from Mint. Use this '
              'pickled epoch instead. If coupled with --dry_run, no '
              'connection to Mint is established.'))

    parser.add_argument(
        '--dry_run', action='store_true',
        help=('Do not modify Mint transaction; instead print the proposed '
              'changes to console.'))

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
        '--description_prefix', type=str,
        default=DEFAULT_MERCHANT_PREFIX,
        help=('The prefix to use when updating the description for each Mint '
              'transaction. Default is "Amazon.com: ". This is nice as it '
              'makes transactions still retrieval by searching "amazon". It '
              'is also used to detecting if a transaction has already been '
              'tagged by this tool.'))
    parser.add_argument(
        '--description_return_prefix', type=str,
        default=DEFAULT_MERCHANT_REFUND_PREFIX,
        help=('The prefix to use when updating the description for each Mint '
              'transaction. Default is "Amazon.com refund: ". This is nice as '
              'it makes transactions still retrieval by searching "amazon". '
              'It is also used to detecting if a transaction has already been '
              'tagged by this tool.'))


if __name__ == '__main__':
    main()
