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
import datetime
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
from currency import micro_usd_nearly_equal, micro_usd_to_usd_float, micro_usd_to_usd_string
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
    )

    orders = amazon.Order.parse_from_csv(args.orders_csv)  
    items = amazon.Item.parse_from_csv(args.items_csv)

    # Remove items from cancelled orders.
    items = [i for i in items if not i.is_cancelled()]
    # Remove items that haven't shipped yet (also aren't charged).
    items = [i for i in items if i.order_status == 'Shipped']
    # Remove items with zero quantity (it happens!)
    items = [i for i in items if i.quantity > 0]
    # Make more Items such that every item is quantity 1.
    items = [si for i in items for si in i.split_by_quantity()]

    logger.info('Matching Amazon Items with Orders')
    amazon.associate_items_with_orders(orders, items)

    refunds = [] if not args.refunds_csv else amazon.Refund.parse_from_csv(args.refunds_csv)

    log_amazon_stats(items, orders, refunds)

    # Only match orders that have items.
    orders = [o for o in orders if o.items]

    mint_client = None

    def close_mint_client():
        if mint_client:
            mint_client.close()

    atexit.register(close_mint_client)

    if args.pickled_epoch:
        mint_trans, mint_category_name_to_id = (
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
        mint_trans = mint.Transaction.parse_from_json(mint_transactions_json)
        dump_trans_and_categories(mint_trans, mint_category_name_to_id, epoch)

    def get_prefix(is_debit):
        return (args.description_prefix if is_debit
                else args.description_return_prefix)

    trans = mint.Transaction.unsplit(mint_trans)
    stats['trans'] = len(trans)
    # Skip t if the original description doesn't contain 'amazon'
    trans = [t for t in trans if 'amazon' in t.omerchant.lower()]
    stats['amazon_in_desc'] = len(trans)
    # Skip t if it's pending.
    trans = [t for t in trans if not t.is_pending]
    stats['pending'] = stats['amazon_in_desc'] - len(trans)
    # Skip t if a category filter is given and t does not match.
    if args.mint_input_categories_filter:
        whitelist = set(args.mint_input_categories_filter.lower().split(','))
        trans = [t for t in trans if t.category.lower() in whitelist ]

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
    num_unshipped = len([o for o in unmatched_orders if not o.shipment_date])

    matched_orders = [o for o in orders if o.matched]
    matched_trans = [t for t in trans if t.orders]
    matched_refunds = [r for r in refunds if r.matched]

    stats['trans_unmatch'] = len(unmatched_trans)
    stats['order_unmatch'] = len(unmatched_orders)
    stats['refund_unmatch'] = len(unmatched_refunds)
    stats['trans_match'] = len(matched_trans)
    stats['order_match'] = len(matched_orders)
    stats['refund_match'] = len(matched_refunds)
    stats['skipped_orders_gift_card'] = num_gift_card
    stats['skipped_orders_unshipped'] = num_unshipped

    merged_orders = []
    merged_refunds = []

    updates = []
    for t in matched_trans:
        if t.is_debit:
            order = amazon.Order.merge(t.orders)
            merged_orders.extend(orders)

            if order.attribute_subtotal_diff_to_misc_charge():
                stats['misc_charge'] += 1
            # It's nice when "free" shipping cancels out with the shipping
            # promo, even though there is tax on said free shipping. Spread
            # that out across the items instead.
            # if order.attribute_itemized_diff_to_shipping_tax():
            #     stats['add_shipping_tax'] += 1
            if order.attribute_itemized_diff_to_per_item_tax():
                stats['adjust_itemized_tax'] += 1

            assert micro_usd_nearly_equal(t.amount, order.total_charged)
            assert micro_usd_nearly_equal(t.amount, order.total_by_subtotals())
            assert micro_usd_nearly_equal(t.amount, order.total_by_items())

            new_transactions = order.to_mint_transactions(
                t,
                skip_free_shipping=not args.verbose_itemize)

        else:
            refunds = amazon.Refund.merge(t.orders)
            merged_refunds.extend(refunds)

            new_transactions = [
                r.to_mint_transaction(t)
                for r in refunds]

        assert micro_usd_nearly_equal(t.amount, mint.Transaction.sum_amounts(new_transactions))

        for nt in new_transactions:
             nt.update_category_id(mint_category_name_to_id)

        prefix = get_prefix(t.is_debit)
        summarize_single_item_order = (
            t.is_debit and len(order.items) == 1 and not args.verbose_itemize)
        if args.no_itemize or summarize_single_item_order:
            new_transactions = mint.summarize_new_trans(t, new_transactions, prefix)
        else:
            new_transactions = mint.itemize_new_trans(new_transactions, prefix)

        if mint.Transaction.old_and_new_are_identical(
                t, new_transactions, ignore_category=args.no_tag_categories):
            stats['already_up_to_date'] += 1
            continue

        if t.merchant.startswith(prefix):
            if args.prompt_retag:
                if args.num_updates > 0 and len(updates) >= args.num_updates:
                    break
                logger.info('\nTransaction already tagged:')
                print_dry_run(
                    [(t, new_transactions)],
                    ignore_category=args.no_tag_categories)
                logger.info('\nUpdate tag to proposed? [Yn] ')
                action = readchar.readchar()
                if action == '':
                    exit(1)
                if action not in ('Y', 'y', '\r', '\n'):
                    stats['user_skipped_retag'] += 1
                    continue
                stats['retag'] += 1
            elif not args.retag_changed:
                stats['no_retag'] += 1
                continue
            else:
                stats['retag'] += 1
        else:
            stats['new_tag'] += 1
        updates.append((t, new_transactions))

    log_processing_stats(stats)

    if not updates:
        logger.info(
            'All done; no new tags to be updated at this point in time!.')
        exit(0)

    if args.num_updates > 0:
        updates = updates[:num_updates]

    if args.dry_run:
        logger.info('Dry run. Following are proposed changes:')
        print_dry_run(updates, ignore_category=args.no_tag_categories)
    else:
        # Ensure we have a Mint client.
        if not mint_client:
            mint_client = get_mint_client(args)

        send_updates_to_mint(
            updates, mint_client, ignore_category=args.no_tag_categories)


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
    start_time = time.time()
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

    dur = s_to_time(time.time() - start_time)
    logger.info('Got {} transactions and {} categories from Mint in {}'.format(
        len(transactions), len(categories), dur))

    return transactions, categories


def log_amazon_stats(items, orders, refunds):
    logger.info('\nAmazon Stats:')
    first_order_date = min([o.order_date for o in orders])
    last_order_date = max([o.order_date for o in orders])
    logger.info('\n{} orders with {} matching items'.format(
        len([o for o in orders if o.items_matched]),
        len([i for i in items if i.matched])))
    logger.info('{} unmatched orders and {} unmatched items'.format(
        len([o for o in orders if not o.items_matched]),
        len([i for i in items if not i.matched])))
    logger.info('Orders ranging from {} to {}'.format(first_order_date, last_order_date))

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
        'Orders matched w/ transactions: {order_match} (unmatched orders: {order_unmatch})\n'
        'Refunds matched w/ transactions: {refund_match} (unmatched refunds: {refund_unmatch})\n'
        'Transactions matched w/ orders/refunds: {trans_match} (unmatched: {trans_unmatch})\n'
        '\n'
        'Orders skipped: not shipped: {skipped_orders_unshipped}\n'
        'Orders skipped: gift card used: {skipped_orders_gift_card}\n'
        '\n'
        'Order fix-up: incorrect tax itemization: {adjust_itemized_tax}\n'
        'Order fix-up: has a misc charges (e.g. gift wrap): {misc_charge}\n'
        '\n'
        'Transactions ignored; already tagged & up to date: {already_up_to_date}\n'
        'Transactions ignored; ignore retags: {no_retag}\n'
        'Transactions ignored; user skipped retag: {user_skipped_retag}\n'
        '\n'
        'Transactions to be retagged: {retag}\n'
        'Transactions to be newly tagged: {new_tag}'.format(**stats))


def print_dry_run(orig_trans_to_tagged, ignore_category=False):
    for orig_trans, new_trans in orig_trans_to_tagged:
        oid = orig_trans.orders[0].order_id
        logger.info('\nFor Amazon {}: {}\nInvoice URL: {}'.format(
            'Order' if orig_trans.is_debit else 'Refund',
            oid, amazon.get_invoice_url(oid)))

        if orig_trans.children:
            for i, trans in enumerate(orig_trans.children):
                logger.info('{}{}) Current: \t{}'.format(
                    '\n' if i == 0 else '',
                    i + 1,
                    trans.dry_run_str()))
        else:
            logger.info('\nCurrent: \t{}'.format(
                orig_trans.dry_run_str()))

        if len(new_trans) == 1:
            trans = new_trans[0]
            logger.info('\nProposed: \t{}'.format(
                trans.dry_run_str(ignore_category)))
        else:
            for i, trans in enumerate(reversed(new_trans)):
                logger.info('{}{}) Proposed: \t{}'.format(
                    '\n' if i == 0 else '',
                    i + 1,
                    trans.dry_run_str(ignore_category)))


def send_updates_to_mint(updates, mint_client, ignore_category=False):
    # TODO:
    #   Unsplits
    #   Send notes for everything

    logger.info('Sending {} updates to Mint.'.format(len(updates)))

    start_time = time.time()
    num_requests = 0
    for (orig_trans, new_trans) in updates:
        if len(new_trans) == 1:
            # Update the existing transaction.
            trans = new_trans[0]
            modify_trans = {
                'task': 'txnedit',
                'txnId': '{}:0'.format(trans.id),
                'note': trans.note,
                'merchant': trans.merchant,
                'token': mint_client.token,
            }
            if not ignore_category:
                modify_trans = {
                    **modify_trans,
                    'category': trans.category,
                    'catId': trans.category_id,
                }

            logger.debug('Sending a "modify" transaction request: {}'.format(
                modify_trans))
            response = mint_client.post(
                '{}{}'.format(
                    MINT_ROOT_URL,
                    UPDATE_TRANS_ENDPOINT),
                data=modify_trans).text
            logger.debug('Received response: {}'.format(response))
            num_requests += 1
        else:
            # Split the existing transaction into many.
            # If the existing transaction is a:
            #   - credit: positive amount is credit, negative debit
            #   - debit: positive amount is debit, negative credit
            itemized_split = {
                'txnId': '{}:0'.format(orig_trans.id),
                'task': 'split',
                'data': '',  # Yup this is weird.
                'token': mint_client.token,
            }
            for (i, trans) in enumerate(new_trans):
                amount = trans.amount
                # Based on the comment above, if the original transaction is a
                # credit, flip the amount sign for things to work out!
                if not orig_trans.is_debit:
                    amount *= -1
                amount = micro_usd_to_usd_float(amount)
                itemized_split['amount{}'.format(i)] = amount
                # Yup. Weird:
                itemized_split['percentAmount{}'.format(i)] = amount
                itemized_split['merchant{}'.format(i)] = trans.merchant
                # Yup weird. '0' means new?
                itemized_split['txnId{}'.format(i)] = 0
                if not ignore_category:
                    itemized_split['category{}'.format(i)] = trans.category
                    itemized_split['categoryId{}'.format(i)] = trans.category_id


            logger.debug('Sending a "split" transaction request: {}'.format(
                itemized_split))
            response = mint_client.post(
                '{}{}'.format(
                    MINT_ROOT_URL,
                    UPDATE_TRANS_ENDPOINT),
                data=itemized_split).text
            logger.debug('Received response: {}'.format(response))
            num_requests += 1

    dur = s_to_time(time.time() - start_time)
    logger.info('Sent {} updates to Mint in {}'.format(num_requests, dur))


def s_to_time(s):
    s = int(s)
    dur_s = int(s % 60)
    dur_m = int(s / 60) % 60
    dur_h = int(s // 60 // 60)
    return datetime.time(hour=dur_h, minute=dur_m, second=dur_s)


def define_args(parser):
    # Mint creds:
    parser.add_argument(
        '--mint_email', default=None,
        help=('Mint e-mail address for login. If not provided here, will be '
              'prompted for user.'))
    parser.add_argument(
        '--mint_password', default=None,
        help=('Mint password for login. If not provided here, will be '
              'prompted for.'))

    # Inputs:
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
        '--dry_run', action='store_true',
        help=('Do not modify Mint transaction; instead print the proposed '
              'changes to console.'))
    parser.add_argument(
        '--num_updates', type=int,
        default=0,
        help=('Only send the first N updates to Mint (or print N updates at '
              'dry run). If not present, all updates are sent or printed.'))

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

    # How to tell when to skip a transaction:
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



if __name__ == '__main__':
    main()
