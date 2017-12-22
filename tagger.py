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
import copy
import csv
import datetime
import itertools
import logging
import pickle
from pprint import pprint
import string
import time

import getpass
import keyring
# Temporary until mintapi is fixed upstream.
from mintapifuture.mintapi.api import Mint, MINT_ROOT_URL
import readchar

import category

logger = logging.getLogger(__name__)
logger.addHandler(logging.StreamHandler())
logger.setLevel(logging.INFO)

AMAZON_CURRENCY_FIELD_NAMES = set([
    'Item Subtotal',
    'Item Subtotal Tax',
    'Item Total',
    'List Price Per Unit',
    'Purchase Price Per Unit',
    'Refund Amount',
    'Refund Tax Amount',
    'Shipping Charge',
    'Subtotal',
    'Tax Charged',
    'Tax Before Promotions',
    'Total Charged',
    'Total Promotions',
])

AMAZON_DATE_FIELD_NAMES = set([
    'Order Date',
    'Refund Date',
    'Shipment Date',
])

# 50 Micro dollars we'll consider equal (this allows for some
# division/multiplication rounding wiggle room).
MICRO_USD_EPS = 50
CENT_MICRO_USD = 10000

DOLLAR_EPS = 0.0001

DEFAULT_MERCHANT_PREFIX = 'Amazon.com: '
DEFAULT_MERCHANT_REFUND_PREFIX = 'Amazon.com refund: '

KEYRING_SERVICE_NAME = 'mintapi'

UPDATE_TRANS_ENDPOINT = '/updateTransaction.xevent'


def pythonify_amazon_dict(dicts):
    if not dicts:
        return dicts
    # Assumes uniform dicts (invariant based on csv library):
    keys = set(dicts[0].keys())
    currency_keys = keys & AMAZON_CURRENCY_FIELD_NAMES
    date_keys = keys & AMAZON_DATE_FIELD_NAMES
    for d in dicts:
        # Convert to microdollar ints
        for ck in currency_keys:
            d[ck] = parse_usd_as_micro_usd(d[ck])
        # Convert to datetime.date
        for dk in date_keys:
            d[dk] = parse_amazon_date(d[dk])
        if 'Quantity' in keys:
            d['Quantity'] = int(d['Quantity'])
    return dicts


def pythonify_mint_dict(dicts):
    for d in dicts:
        # Parse out the date fields into datetime.date objects.
        d['date'] = parse_mint_date(d['date'])
        d['odate'] = parse_mint_date(d['odate'])

        # Parse the amount into micro usd.
        amount = parse_usd_as_micro_usd(d['amount'])
        # Adjust credit transactions such that:
        # - debits are positive
        # - credits are negative
        if not d['isDebit']:
            amount *= -1
        d['amount'] = amount

    return dicts


def parse_amazon_date(date_str):
    if not date_str:
        return None
    try:
        return datetime.datetime.strptime(date_str, '%m/%d/%Y').date()
    except ValueError:
        return datetime.datetime.strptime(date_str, '%m/%d/%y').date()


def parse_mint_date(dateraw):
    cy = datetime.datetime.isocalendar(datetime.date.today())[0]
    try:
        newdate = datetime.datetime.strptime(dateraw + str(cy), '%b %d%Y')
    except:
        newdate = datetime.datetime.strptime(dateraw, '%m/%d/%y')
    return newdate.date()


def round_usd(curr):
    return round(curr + DOLLAR_EPS, 2)


def round_micro_usd_to_cent(micro_usd):
    return int(round_usd(micro_usd_to_usd_float(micro_usd)) * 1000000)


def micro_usd_to_usd_float(micro_usd):
    return round_usd(micro_usd / 1000000.0)


def micro_usd_to_usd_string(micro_usd):
    return '{}${:.2f}'.format(
        '' if micro_usd > 0 else '-',
        micro_usd_to_usd_float(abs(micro_usd)))


def parse_usd_as_micro_usd(amount):
    return int(round_usd(parse_usd_as_float(amount)) * 1000000)


def parse_usd_as_float(amount):
    if not amount:
        return 0.0
    # Remove any formatting/grouping commas.
    amount = amount.replace(',', '')
    if '$' == amount[0]:
        amount = amount[1:]
    try:
        return float(amount)
    except ValueError:
        return 0.0


def adjust_amazon_item_quantity(item, new_quantity):
    original_quantity = item['Quantity']

    assert new_quantity > 0
    assert new_quantity <= original_quantity
    assert (item['Purchase Price Per Unit'] * original_quantity ==
            item['Item Subtotal'])

    item['Item Subtotal'] = item['Purchase Price Per Unit'] * new_quantity
    item['Item Subtotal Tax'] = (
        item['Item Subtotal Tax'] / original_quantity) * new_quantity
    item['Item Total'] = item['Item Subtotal'] + item['Item Subtotal Tax']
    item['Quantity'] = new_quantity

    # Tag the item as being modified.
    item['ORIGINAL_QUANTITY_IN_ORDER'] = original_quantity


printable = set(string.printable)


def get_item_title(item, target_length):
    # Also works for a Refund record.
    qty = item['Quantity']
    base_str = None
    if qty > 1:
        base_str = str(qty) + 'x'
    # Remove non-ASCII characters from the title.
    clean_title = ''.join(filter(lambda x: x in printable, item['Title']))
    return truncate_title(clean_title, target_length, base_str)


def truncate_title(title, target_length, base_str=None):
    words = []
    if base_str:
        words.extend([w for w in base_str.split(' ') if w])
        target_length -= len(base_str)
    for word in title.split(' '):
        if len(word) / 2 < target_length:
            words.append(word)
            target_length -= len(word) + 1
        else:
            break
    truncated = ' '.join(words)
    # Remove any trailing symbol-y crap.
    while truncated and truncated[-1] in ',.-()[]{}\/|~!@#$%^&*_+=`\'" ':
        truncated = truncated[:-1]
    return truncated


def get_notes_header(order):
    return ('Amazon order id: {}\n'
            'Order date: {}\n'
            'Ship date: {}\n'
            'Tracking: {}').format(
        order['Order ID'],
        order['Order Date'],
        order['Shipment Date'],
        order['Carrier Name & Tracking Number'])


def get_refund_notes_header(refund):
    return ('Amazon refund for order id: {}\n'
            'Order date: {}\n'
            'Refund date: {}\n'
            'Refund reason: {}').format(
        refund['Order ID'],
        refund['Order Date'],
        refund['Refund Date'],
        refund['Refund Reason'])


def sum_amounts(trans):
    return sum([t['amount'] for t in trans])


def log_amazon_stats(items, orders, refunds):
    logger.info('\nAmazon Stats:')
    first_order_date = min([o['Order Date'] for o in orders])
    last_order_date = max([o['Order Date'] for o in orders])
    logger.info('\n{} orders & {} items dating from {} to {}'.format(
        len(orders), len(items), first_order_date, last_order_date))

    per_item_totals = [i['Item Total'] for i in items]
    per_order_totals = [o['Total Charged'] for o in orders]

    logger.info('{} total spend'.format(
        micro_usd_to_usd_string(sum(per_order_totals))))

    logger.info('{} avg order charged (max: {})'.format(
        micro_usd_to_usd_string(sum(per_order_totals) / len(orders)),
        micro_usd_to_usd_string(max(per_order_totals))))
    logger.info('{} avg item price (max: {})'.format(
        micro_usd_to_usd_string(sum(per_item_totals) / len(items)),
        micro_usd_to_usd_string(max(per_item_totals))))

    if refunds:
        first_refund_date = min(
            [r['Refund Date'] for r in refunds if r['Refund Date']])
        last_refund_date = max(
            [r['Refund Date'] for r in refunds if r['Refund Date']])
        logger.info('\n{} refunds dating from {} to {}'.format(
            len(refunds), first_refund_date, last_refund_date))

        per_refund_totals = [r['Total Refund Amount'] for r in refunds]

        logger.info('{} total refunded'.format(
            micro_usd_to_usd_string(sum(per_refund_totals))))


def log_processing_stats(stats, prefix):
    logger.info(
        '\nTransactions w/ "Amazon" in description: {}\n'
        '\n'
        'Transactions ignored: is pending: {}\n'
        'Transactions ignored: item quantity mismatch: {}\n'
        '\n'
        'Transactions w/ matching order information: {} (unmatched orders: {})\n'
        'Transactions w/ matching refund information: {} (unmatched refunds: {})\n'
        '\n'
        'Orders skipped: not shipped: {}\n'
        'Orders skipped: gift card used: {}\n'
        '\n'       
        'Order fix-up: itemization quantity tinkering: {}\n'
        'Order fix-up: incorrect tax itemization: {}\n'
        'Order fix-up: has a misc charges (e.g. gift wrap): {}\n'
        '\n'
        'Transactions w/ proposed tags/itemized: {}\n'
        '\n'
        'Transactions ignored; already tagged & up to date: {}\n'
        'Transactions ignored; already has prefix "{}" or "{}": {}\n'
        '\n'
        'Transactions to be updated: {}'.format(
            stats['amazon_in_desc'],
            stats['pending'],
            stats['orders_need_combinatoric_adjustment'],
            stats['order_match'],
            stats['order_unmatch'],
            stats['refund_match'],
            stats['refund_unmatch'],
            stats['skipped_orders_unshipped'],
            stats['skipped_orders_gift_card'],
            stats['quantity_adjust'],
            stats['items_tax_adjust'],
            stats['misc_charge'],
            stats['tagged'],
            stats['no_change'],
            prefix(True),
            prefix(False),
            stats['already_has_prefix'],
            stats['to_be_updated']))


class MintTransWrapper(object):
    """A wrapper for Mint tranactions, primarily for change detection."""
    def __init__(self, d):
        self.d = d

    def get_tuple(self):
        # TODO: Add the 'note' field once itemized transactions include notes.
        return (
            self.d['merchant'],
            micro_usd_to_usd_string(self.d['amount']),  # str avoids float cmp
            self.d['category'])

    def __repr__(self):
        return repr(self.get_tuple())

    def __hash__(self):
        return hash(self.get_tuple())

    def __eq__(self, other):
        return self.get_tuple() == other.get_tuple()

    def __ne__(self, other):
        return not(self == other)


def tag_as_order(
        t, matched_orders, tracking_to_items, order_id_to_items, stats):
    # Only consider it a match if the posted date (transaction date) is
    # within 3 days of the ship date of the order.
    closest_match = None
    closest_match_num_days = 365  # Large number
    for orders in matched_orders:
        an_order = next(o for o in orders if o['Shipment Date'])
        num_days = (t['odate'] - an_order['Shipment Date']).days
        # TODO: consider orders even if it has a matched_transaction if this
        # transaction is closer.
        already_matched = any(['MATCHED_TRANSACTION' in o for o in orders])
        if (abs(num_days) < 4 and
                abs(num_days) < closest_match_num_days and
                not already_matched):
            closest_match = orders
            closest_match_num_days = abs(num_days)

    if not closest_match:
        logger.debug(
            'Cannot find viable order matching transaction {0}'.format(t))
        return None
    stats['order_match'] += 1

    logger.debug(
        'Found a match: {0} for transaction: {1}'.format(
            closest_match, t))
    orders = closest_match
    # Prevent future transactions matching up against this order.
    new_trans = []
    t['MATCHED_ORDER_ID'] = orders[0]['Order ID']
    for o in orders:
        o['MATCHED_TRANSACTION'] = t
        o['MATCH_TYPE'] = 'Multi' if len(orders) > 1 else 'Single'
        new_trans.extend(
            tag_order(t, o, tracking_to_items, order_id_to_items, stats) or [])
        
    if sum_amounts([t]) - sum_amounts(new_trans) >= MICRO_USD_EPS:
        # Had an issue with one of the orders! Unmark the transaction & orders.
        stats['item_mismatch'] += 1
        for o in orders:
            del o['MATCHED_TRANSACTION']
            del o['MATCH_TYPE']
        del t['MATCHED_ORDER_ID']
        return []

    return new_trans


def tag_order(t, order, tracking_to_items, order_id_to_items, stats):
    # Use the shipping no. (and also verify the order number) to cross
    # reference/find all the items in that shipment.
    # Order number cannot be used alone, as multiple shipments (and thus
    # charges) can be associated with the same order #.
    tracking = order['Carrier Name & Tracking Number']
    order_id = order['Order ID']
    items = []
    if not tracking or tracking not in tracking_to_items:
        # This happens either:
        #   a) When an order contains a quantity of one item greater than 1,
        #      and the items get split between multiple shipments. As such,
        #      only 1 tracking number is in the map correctly. For the
        #      other shipment (and thus charge), the item must be
        #      re-associated.
        #   b) No tracking number is required. This is almost always a
        #      digital good/download.
        if order_id not in order_id_to_items:
            return None
        items = order_id_to_items[order_id]
        if not items:
            return None

        item = None
        for i in items:
            if i['Purchase Price Per Unit'] == order['Subtotal']:
                item = copy.deepcopy(i)
                adjust_amazon_item_quantity(item, 1)
                diff = order['Total Charged'] - item['Item Total']
                if diff and abs(diff) < 10000:
                    item['Item Total'] += diff
                    item['Item Subtotal Tax'] += diff
                stats['quantity_adjust'] += 1
                break

        if not item:
            stats['orders_need_combinatoric_adjustment'] += 1
            return None

        items = [item]
    else:
        # Be sure to filter out other orders, as items from multiple orders
        # can indeed be packed/shipped together (but charged
        # independently).
        items = [i
                 for i in tracking_to_items[tracking]
                 if i['Order ID'] == order_id]

    if not items:
        return None

    for i in items:
        assert i['Order ID'] == order_id

    # More expensive items are always more interesting when it comes to
    # budgeting, so show those first (for both itemized and concatted).
    items = sorted(items, key=lambda item: item['Item Total'], reverse=True)

    new_transactions = []

    # Do a quick check to ensure all the item sub-totals add up to the
    # order sub-total.
    items_sum = sum([i['Item Subtotal'] for i in items])
    order_total = order['Subtotal']
    if abs(items_sum - order_total) > DOLLAR_EPS:
        # Uh oh, the sub-totals weren't equal. Try to fix, skip is not
        # possible.
        if len(items) == 1:
            # If there's only one item, typically the quantity in this
            # charge/shipment was less than the total quantity
            # ordered.  Copy this item as this case is highly like
            # that the item spans multiple shipments. Having the
            # original item w/ the original quantity is quite useful
            # for the other half of the order.
            found_quantity = False
            items[0] = item = copy.deepcopy(items[0])
            quantity = item['Quantity']
            per_unit = item['Purchase Price Per Unit']
            for i in range(quantity):
                if per_unit * i == order['Subtotal']:
                    found_quantity = True
                    adjust_amazon_item_quantity(item, i)
                    diff = order['Total Charged'] - item['Item Total']
                    if diff and abs(diff) < 10000:
                        item['Item Total'] += diff
                        item['Item Subtotal Tax'] += diff
                    break
            if not found_quantity:
                # Unable to adjust this order. Drop it.
                return None
        else:
            # TODO: Find the combination of items that add up to the
            # sub-total amount.
            stats['orders_need_combinatoric_adjustment'] += 1
            return None

    # Itemize line-items:
    for i in items:
        item = copy.deepcopy(t)
        item['merchant'] = get_item_title(i, 88)
        item['category'] = category.AMAZON_TO_MINT_CATEGORY.get(
            i['Category'], category.DEFAULT_MINT_CATEGORY)
        item['amount'] = i['Item Total']
        item['isDebit'] = True
        item['note'] = get_notes_header(order)

        new_transactions.append(item)

    # Itemize the shipping cost, if any.
    ship = None
    if order['Shipping Charge']:
        ship = copy.deepcopy(t)

        # Shipping has tax. Include this in the shipping line item, as this
        # is how the order items are done. Unfortunately, this isn't broken
        # out anywhere, so compute it.
        ship_tax = order['Tax Charged'] - sum(
            [i['Item Subtotal Tax'] for i in items])

        ship['merchant'] = 'Shipping'
        ship['category'] = 'Shipping'
        ship['amount'] = order['Shipping Charge'] + ship_tax
        ship['isDebit'] = True
        ship['note'] = get_notes_header(order)

        new_transactions.append(ship)

    # All promotion(s) as one line-item.
    promo = None
    if order['Total Promotions']:
        promo = copy.deepcopy(t)
        promo['merchant'] = 'Promotion(s)'
        promo['category'] = category.DEFAULT_MINT_CATEGORY
        promo['amount'] = -order['Total Promotions']
        promo['isDebit'] = False
        promo['note'] = get_notes_header(order)

        new_transactions.append(promo)

    # If there was a promo that matches the shipping cost, it's nearly
    # certainly a Free One-day/same-day/etc promo. In this case, categorize
    # the promo instead as 'Shipping', which will cancel out in Mint
    # trends.

    # Also, check if tax was computed before or after the promotion was
    # applied. If the latter, attribute the difference to the
    # promotion. This only applies if the promotion is not free shipping.
    #
    # TODO: Clean this up. Turns out Amazon doesn't correctly set
    # 'Tax Before Promotions' now adays. Not sure why?!
    tax_diff = order['Tax Before Promotions'] - order['Tax Charged']
    if promo and ship and abs(promo['amount']) == ship['amount']:
        promo['category'] = 'Shipping'
    elif promo and tax_diff:
        promo['amount'] = promo['amount'] - tax_diff

    # Check that the total of the itemized transactions equals that of the
    # original (this now includes things like: tax, promotions, and
    # shipping).
    itemized_sum = sum_amounts(new_transactions)
    itemized_diff = order['Total Charged'] - itemized_sum
    if abs(itemized_diff) > MICRO_USD_EPS:
        itemized_tax = sum([i['Item Subtotal Tax'] for i in items])
        tax_diff = order['Tax Before Promotions'] - itemized_tax
        if itemized_diff - tax_diff < MICRO_USD_EPS:
            # Well, that's funny. The per-item tax was not computed
            # correctly; the tax miscalculation matches the itemized
            # difference. Sometimes AMZN is bad at math (lol). To keep the
            # line items adding up correctly, add a new tax miscalculation
            # adjustment, as it's nearly impossibly to find the correct
            # item to adjust (unless there's only one).
            stats['items_tax_adjust'] += 1

            # Not the optimal algorithm... but works.
            # Rounding forces the extremes to be corrected, but when
            # roughly equal, will take from the more expensive items (as
            # those are ordered first).
            tax_rate_per_item = [
                round(i['Item Subtotal Tax'] * 100.0 / i['Item Subtotal'], 1)
                for i in items]
            while abs(tax_diff) > MICRO_USD_EPS:
                if tax_diff > 0:
                    min_idx = None
                    min_rate = None
                    for (idx, rate) in enumerate(tax_rate_per_item):
                        if rate != 0 and (not min_rate or rate < min_rate):
                            min_idx = idx
                            min_rate = rate
                    items[min_idx]['Item Subtotal Tax'] += CENT_MICRO_USD
                    items[min_idx]['Item Total'] += CENT_MICRO_USD
                    new_transactions[min_idx]['amount'] += CENT_MICRO_USD
                    tax_diff -= CENT_MICRO_USD
                    tax_rate_per_item[min_idx] = round(
                        items[min_idx]['Item Subtotal Tax'] * 100.0 /
                        items[min_idx]['Item Subtotal'], 1)
                else:
                    # Find the highest taxed item (by rate) and
                    # discount it a penny.
                    (max_idx, _) = max(
                        enumerate(tax_rate_per_item), key=lambda x: x[1])
                    items[max_idx]['Item Subtotal Tax'] -= CENT_MICRO_USD
                    items[max_idx]['Item Total'] -= CENT_MICRO_USD
                    new_transactions[max_idx]['amount'] -= CENT_MICRO_USD
                    tax_diff += CENT_MICRO_USD
                    tax_rate_per_item[max_idx] = round(
                        items[max_idx]['Item Subtotal Tax'] * 100.0 /
                        items[max_idx]['Item Subtotal'], 1)
        else:
            # The only examples seen at this point are due to gift wrap
            # fees. There must be other corner cases, so let's itemize with a
            # vague line item.
            stats['misc_charge'] += 1

            adjustment = copy.deepcopy(t)
            adjustment['merchant'] = 'Misc Charge (Gift wrap, etc)'
            adjustment['category'] = category.DEFAULT_MINT_CATEGORY
            adjustment['amount'] = itemized_diff
            adjustment['isDebit'] = True
            adjustment['note'] = get_notes_header(order)

            new_transactions.append(adjustment)

    return new_transactions


def tag_as_refund(t, refunds, stats):
    # Only consider it a match if the posted date (transaction date) is
    # within 3 days of the date of the refund.
    closest_match = None
    closest_match_num_days = 365  # Large number

    for r in refunds:
        a_refund = next(d for d in r if d['Refund Date'])
        if not a_refund:
            continue
        num_days = (t['odate'] - a_refund['Refund Date']).days
        # TODO: consider r even if it has a matched_transaction if this
        # transaction is closer.
        if (abs(num_days) < 4 and
                abs(num_days) < closest_match_num_days and
                not any(['MATCHED_TRANSACTION' in rf for rf in r])):
            closest_match = r
            closest_match_num_days = abs(num_days)

    if not closest_match:
        logger.debug(
            'Cannot find viable refund(s) matching transaction {0}'.format(t))
        return None
    stats['refund_match'] += 1

    logger.debug(
        'Found a match: {0} for transaction: {1}'.format(
            closest_match, t))
    refunds = closest_match
    # Prevent future transactions matching up against these refund(s).
    t['MATCHED_ORDER_ID'] = refunds[0]['Order ID']
    for r in refunds:
        r['MATCHED_TRANSACTION'] = t
        r['MATCH_TYPE'] = 'Multi' if len(refunds) > 1 else 'Single'

    # Group items by and use Quantity
    refunds = collapse_items_into_quantity(refunds)

    new_transactions = []

    for r in refunds:
        item = copy.deepcopy(t)
        item['merchant'] = get_item_title(r, 88)
        item['category'] = category.AMAZON_TO_MINT_CATEGORY.get(
            r['Category'], category.DEFAULT_MINT_RETURN_CATEGORY)
        item['amount'] = -r['Total Refund Amount']
        item['isDebit'] = False
        item['note'] = get_refund_notes_header(r)
        # Used in the itemize logic downstream.
        item['IS_REFUND'] = True

        new_transactions.append(item)

    return new_transactions


def collapse_items_into_quantity(items):
    if len(items) <= 1:
        return items
    items_by_name = defaultdict(list)
    for i in items:
        key = '{}-{}-{}-{}-{}-{}'.format(
            i['Refund Date'],
            i['Refund Reason'],
            i['Title'],
            i['Total Refund Amount'],
            i['ASIN/ISBN'],
            i['Quantity'])
        items_by_name[key].append(i)
    results = []
    for same_items in items_by_name.values():
        qty = len(same_items)
        if qty == 1:
            results.extend(same_items)
            continue
        new_item = copy.deepcopy(same_items[0])
        new_item['Quantity'] = qty
        new_item['Total Refund Amount'] *= qty
        new_item['Refund Amount'] *= qty
        new_item['Refund Tax Amount'] *= qty
        results.append(new_item)
    return results


def unsplit_transactions(trans, stats):
    # Reconsistitute Mint splits/itemizations into the parent transaction.
    parent_id_to_trans = defaultdict(list)
    result = []
    for t in trans:
        if t['isChild']:
            parent_id_to_trans[t['pid']].append(t)
        else:
            result.append(t)

    for p_id, children in parent_id_to_trans.items():
        parent = copy.deepcopy(children[0])

        parent['id'] = p_id
        parent['isChild'] = False
        del parent['pid']
        parent['amount'] = round_micro_usd_to_cent(sum_amounts(children))
        parent['isDebit'] = parent['amount'] > 0
        parent['CHILDREN'] = children

        result.append(parent)

    return result


def tag_transactions(
        items, orders, refunds, trans, itemize, prefix, stats):
    """Matches up Mint transactions with Amazon orders and itemizes the orders.

    Args:
        - items: list of dict objects. The user's Amazon items report. Each
          row is an item from an order. Items have quantities. More
          interestingly, if an order (see note below) is fulfilled in multiple
          shipments and an item with a quantity greater than 1 is split into
          multiple shipments, there is still only one item object corresponding
          to it. In this case, the tracking matches only 1 of the shipments.
        - orders: list of dict objects. The user's Amazon orders
          report. Each row is an order, or X rows per order when split into X
          shipments (due to partial fulfillment or special shipping
          requirements).
        - refunds: list of dict objects. The user's Amazon refunds report. Each
          row is a refund.
        - trans: list of dicts. The user's Mint transactions.
        - itemize: bool. True will split a Mint transaction into per-item
          breakouts, and attempting to guess the appropriate category based on
          the Amazon item's category.
        - prefix: callable. Returns the prefix string to use for a debit or
          credit. Takes one arg: boolean: isDebit.
        - stats: Counter. Used for accumulating processing stats throughout the
          tool.

    Returns:
        A list of 2-tuples: [(existing trans, list[tagged trans, ..]), ...]
        Entries are only in the output if they have been successfully matched
        and validated with an Amazon order and properly itemized (or
        summarized).
    """
    # Skip orders if they haven't shipped yet (almost certainly not charged yet).
    num_orders = len(orders)
    orders = [o for o in orders if o['Shipment Date']]
    stats['skipped_orders_unshipped'] = num_orders - len(orders)

    # A multi-map from charged amount to a list of orders.
    amount_to_orders = defaultdict(list)
    for o in orders:
        charged = o['Total Charged']
        amount_to_orders[charged].append([o])

    # Sometimes orders get merged together and charged as one. This especially
    # happens for marketplace transacitons. Group orders by id, then find all
    # possible combinations and insert those as candidates to match against
    # transactions. It is exponential, so fingers crossed there are no mega
    # orders.
    orders_by_id = defaultdict(list)
    num_combo_branches = 0
    for o in orders:
        orders_by_id[o['Order ID']].append(o)
    for orders_same_id in orders_by_id.values():
        combos = []
        for r in range(2, len(orders_same_id) + 1):
            combos.extend(itertools.combinations(orders_same_id, r))
        for c in combos:
            orders_total = sum([o['Total Charged'] for o in c])
            amount_to_orders[orders_total].append(c)
            num_combo_branches += 1

    # A multi-map from tracking id to items.
    # Note: on lookup, be sure to restrict results to just one order id, as
    # Amazon does merge orders into the same box.
    tracking_to_items = defaultdict(list)
    for i in items:
        tracking = i['Carrier Name & Tracking Number']
        tracking_to_items[tracking].append(i)

    # A multi-map from order id to items.
    order_id_to_items = defaultdict(list)
    for i in items:
        id = i['Order ID']
        order_id_to_items[id].append(i)

    # A multi-map from refunded amount to a list of refunds.
    # This will get weird, as AMZN likes to break out refunds on a per item
    # basis (you send back 3 of item X, you'll see 3 rows of items X w/
    # quantity 1).
    amount_to_refunds = defaultdict(list)
    for r in refunds:
        amount = r['Total Refund Amount']
        amount_to_refunds[amount].append([r])

    # Collapse all returns from the same order into one:
    refund_order_id_to_refunds = defaultdict(list)
    for r in refunds:
        refund_order_id_to_refunds[r['Order ID']].append(r)
    for refunds_for_order in refund_order_id_to_refunds.values():
        if len(refunds_for_order) == 1:
            continue

        # Don't dupe with the other method (same order & same day):
        if len(set([r['Refund Date'] for r in refunds_for_order])) <= 1:
            continue

        refund_total = sum(
            [r['Total Refund Amount'] for r in refunds_for_order])
        amount_to_refunds[refund_total].append(refunds_for_order)

    # Collapse all returns from the same order and same return date into one:
    same_day_to_refunds = defaultdict(list)
    for r in refunds:
        key = '{}_{}'.format(r['Order ID'], r['Refund Date'])
        same_day_to_refunds[key].append(r)
    for refunds_for_order in same_day_to_refunds.values():
        if len(refunds_for_order) == 1:
            continue
        refund_total = sum(
            [r['Total Refund Amount'] for r in refunds_for_order])
        amount_to_refunds[refund_total].append(refunds_for_order)

    result = []

    # Skip t if the original description doesn't contain 'amazon'
    trans = [t for t in trans if 'amazon' in t['omerchant'].lower()]
    stats['amazon_in_desc'] = len(trans)
    # Skip t if it's pending.
    trans = [t for t in trans if not t['isPending']]
    stats['pending'] = stats['amazon_in_desc'] - len(trans)

    trans = unsplit_transactions(trans, stats)

    for t in trans:
        # Find an exact match by amount.
        amount = t['amount']
        new_trans = []
        if t['isDebit'] and amount in amount_to_orders:
            new_trans = tag_as_order(
                t, amount_to_orders.get(amount), tracking_to_items,
                order_id_to_items, stats)
        elif not t['isDebit'] and -amount in amount_to_refunds:
            new_trans = tag_as_refund(t, amount_to_refunds.get(-amount), stats)
        else:
            logger.debug('Cannot find purchase for transaction: {0}'.format(t))
            # Look at additional matching strategies?
            continue
        
        if not new_trans:
            continue

        # Use the original transaction to determine if this overall is a
        # purchase or refund.
        prefix_str = prefix(t['isDebit'])
        result.append(
            (t, (itemize_new_trans(new_trans, prefix_str) if itemize
                 else summarize_new_trans(t, new_trans, prefix_str))))

    unmatched_orders = [o for o in orders if 'MATCHED_TRANSACTION' not in o]
    num_unmatched = len(unmatched_orders)
    unmatched_orders = [o for o in unmatched_orders
                        if 'Gift Certificate' not in o['Payment Instrument Type']]

    # Count orders if the used a gift card. This includes if a gift card was
    # only partially used, as the reports are not expressive enough to know
    # what portion was charged to card. Sometimes store card credits or Amazon
    # Payments count as a 'gift card'.
    stats['skipped_orders_gift_card'] = num_unmatched - len(unmatched_orders)

    stats['order_unmatch'] = len(unmatched_orders)
    stats['refund_unmatch'] = len(
        [r for r in refunds if 'MATCHED_TRANSACTION' not in r])

    return result


def itemize_new_trans(new_trans, prefix):
    # Add a prefix to all itemized transactions for easy keyword searching
    # within Mint. Use the same prefix, based on if the original transaction
    for nt in new_trans:
        nt['merchant'] = prefix + nt['merchant']

    # Turns out the first entry is typically displayed last in the Mint
    # UI. Reverse everything for ideal readability.
    return new_trans[::-1]


def summarize_new_trans(t, new_trans, prefix):
    # When not itemizing, create a description by concating the items. Store
    # the full information in the transaction notes. Category is untouched when
    # there's more than one item (this is why itemizing is better!).
    trun_len = (100 - len(prefix) - 2 * len(new_trans)) / len(new_trans)
    title = prefix + (', '.join(
        [truncate_title(nt['merchant'], trun_len)
         for nt in new_trans
         if nt['merchant'] not in
         ('Promotion(s)', 'Shipping', 'Tax adjustment')]))
    notes = '{}\nItem(s):\n{}'.format(
        new_trans[0]['note'],
        '\n'.join(
            [' - ' + nt['merchant']
             for nt in new_trans]))

    summary_trans = copy.deepcopy(t)
    summary_trans['merchant'] = title
    if len(new_trans) == 1:
        summary_trans['category'] = new_trans[0]['category']
    else:
        summary_trans['category'] = category.DEFAULT_MINT_CATEGORY
    summary_trans['note'] = notes
    return [summary_trans]


def print_dry_run(orig_trans_to_tagged):
    for orig_trans, new_trans in orig_trans_to_tagged:
        if 'CHILDREN' in orig_trans:
            for i, trans in enumerate(orig_trans['CHILDREN']):
                logger.info('{}{}) Current: {} \t {} \t {} \t {}'.format(
                    '\n' if i == 0 else '',
                    i + 1,
                    trans['date'].strftime('%m/%d/%y'),
                    micro_usd_to_usd_string(trans['amount']),
                    trans['category'],
                    trans['merchant']))
        else:
            logger.info('\nCurrent:  {} \t {} \t {} \t {}'.format(
                orig_trans['date'].strftime('%m/%d/%y'),
                micro_usd_to_usd_string(orig_trans['amount']),
                orig_trans['category'],
                orig_trans['merchant']))

        if len(new_trans) == 1:
            trans = new_trans[0]
            logger.info('\nProposed: {} \t {} \t {} \t {} {}'.format(
                trans['date'].strftime('%m/%d/%y'),
                micro_usd_to_usd_string(trans['amount']),
                trans['category'],
                trans['merchant'],
                ('with details in "Notes"'
                 if orig_trans['note'] != trans['note'] else '')))
        else:
            for i, trans in enumerate(reversed(new_trans)):
                logger.info('{}{}) Proposed: {} \t {} \t {} \t {}'.format(
                    '\n' if i == 0 else '',
                    i + 1,
                    trans['date'].strftime('%m/%d/%y'),
                    micro_usd_to_usd_string(trans['amount']),
                    trans['category'],
                    trans['merchant']))


def write_tags_to_mint(orig_trans_to_tagged, mint_client):
    logger.info('Sending {} updates to Mint.'.format(
        len(orig_trans_to_tagged)))

    start_time = time.time()
    num_requests = 0
    for (orig_trans, new_trans) in orig_trans_to_tagged:
        if len(new_trans) == 1:
            # Update the existing transaction.
            trans = new_trans[0]
            modify_trans = {
                'task': 'txnedit',
                'txnId': '{}:0'.format(trans['id']),
                'note': trans['note'],
                'merchant': trans['merchant'],
                'category': trans['category'],
                'catId': trans['categoryId'],
                'token': mint_client.token,
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
                'txnId': '{}:0'.format(orig_trans['id']),
                'task': 'split',
                'data': '',  # Yup this is weird.
                'token': mint_client.token,
            }
            for (i, trans) in enumerate(new_trans):
                amount = trans['amount']
                # Based on the comment above, if the original transaction is a
                # credit, flip the amount sign for things to work out!
                if not orig_trans['isDebit']:
                    amount *= -1
                amount = micro_usd_to_usd_float(amount)
                itemized_split['amount{}'.format(i)] = amount
                # Yup. Weird:
                itemized_split['percentAmount{}'.format(i)] = amount
                itemized_split['category{}'.format(i)] = trans['category']
                itemized_split['categoryId{}'.format(i)] = trans['categoryId']
                itemized_split['merchant{}'.format(i)] = trans['merchant']
                # Yup weird. '0' means new?
                itemized_split['txnId{}'.format(i)] = 0

            logger.debug('Sending a "split" transaction request: {}'.format(
                itemized_split))
            response = mint_client.post(
                '{}{}'.format(
                    MINT_ROOT_URL,
                    UPDATE_TRANS_ENDPOINT),
                data=itemized_split).text
            logger.debug('Received response: {}'.format(response))
            num_requests += 1

    end_time = time.time()
    dur_total_s = int(end_time - start_time)
    dur_s = int(dur_total_s % 60)
    dur_m = int(dur_total_s / 60) % 60
    dur_h = int(dur_total_s // 60 // 60)
    dur = datetime.time(hour=dur_h, minute=dur_m, second=dur_s)
    logger.info('Sent {} updates to Mint in {}'.format(num_requests, dur))


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


def parse_amazon_csv(args):
    # Parse out Amazon reports (csv files). Do this first so any issues here
    # percolate before going to the cloudz for Mint.
    logger.info('Processing Amazon csv\'s.')
    amazon_items = pythonify_amazon_dict(
        list(csv.DictReader(args.items_csv)))
    amazon_orders = pythonify_amazon_dict(
        list(csv.DictReader(args.orders_csv)))
    amazon_refunds = []
    if args.refunds_csv:
        amazon_refunds = pythonify_amazon_dict(
            list(csv.DictReader(args.refunds_csv)))

    # Refunds are rad: AMZN doesn't total the tax + sub-total for you.
    for ar in amazon_refunds:
        ar['Total Refund Amount'] = (
            ar['Refund Amount'] + ar['Refund Tax Amount'])

    # Sort everything for good measure/consistency/stable ordering.
    amazon_items = sorted(amazon_items, key=lambda item: item['Order Date'])
    amazon_orders = sorted(
        amazon_orders, key=lambda order: order['Order Date'])
    amazon_refunds = sorted(
        amazon_refunds, key=lambda order: order['Order Date'])

    return amazon_items, amazon_orders, amazon_refunds


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
    transactions = pythonify_mint_dict(mint_client.get_transactions_json(
        start_date=start_date_str,
        include_investment=False,
        skip_duplicates=True))

    return transactions, categories


def sanity_check_and_filter_tags(
        orig_trans_to_tagged, mint_category_name_to_id, get_prefix,
        args, stats):
    # Assert old trans amount == sum new trans amount.
    for orig_trans, new_trans in orig_trans_to_tagged:
        if abs(
            sum_amounts(
                [orig_trans]) - sum_amounts(new_trans)) >= MICRO_USD_EPS:
            print(sum_amounts([orig_trans]))
            print(sum_amounts(new_trans))

            pprint(orig_trans)
            pprint(new_trans)

        assert abs(
            sum_amounts([orig_trans]) - sum_amounts(new_trans)) < MICRO_USD_EPS

    # Assert new transactions have valid categories and update the categoryId
    # based on name.
    for orig_trans, new_trans in orig_trans_to_tagged:
        for trans in new_trans:
            assert trans['category'] in mint_category_name_to_id
            trans['categoryId'] = mint_category_name_to_id[trans['category']]

    def original_and_new_are_diff(item):
        orig_trans, new_trans = item
        orig = set(
            [MintTransWrapper(orig_trans)]
            if 'CHILDREN' not in orig_trans
            else [MintTransWrapper(t) for t in orig_trans['CHILDREN']])
        new = set([MintTransWrapper(t) for t in new_trans])

        return orig != new

    # Filter out unchanged entries to avoid duplicate work.
    filtered = list(filter(original_and_new_are_diff, orig_trans_to_tagged))
    stats['no_change'] = len(orig_trans_to_tagged) - len(filtered)

    def orig_missing_prefix(item):
        orig_trans, _ = item
        return not orig_trans['merchant'].startswith(
            get_prefix(orig_trans['isDebit']))
    
    def prompt_if_has_prefix(item):
        orig_trans, new_trans = item
        if not orig_trans['merchant'].startswith(
            get_prefix(orig_trans['isDebit'])):
            return True
        logger.info('\nTransaction already tagged:')
        print_dry_run([item])
        logger.info('\nUpdate tag to proposed? [Yn] ')
        action = readchar.readchar()
        return action in ('Y', 'y', '\r', '\n')
    
    num_before = len(filtered)
    if args.prompt_retag:
        filtered = list(filter(prompt_if_has_prefix, filtered))
    # The user doesn't want any changes from last run if the original
    # transaction already starts with the merchant prefix.
    elif not args.retag_changed:
        filtered = list(filter(orig_missing_prefix, filtered))

    stats['already_has_prefix'] = num_before - len(filtered)
    stats['to_be_updated'] = len(filtered)
    return filtered


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


def main():
    parser = argparse.ArgumentParser(
        description='Tag Mint transactions based on itemized Amazon history.')
    define_args(parser)
    args = parser.parse_args()

    if args.dry_run:
        logger.info('Dry Run; no modifications being sent to Mint.')

    amazon_items, amazon_orders, amazon_refunds = parse_amazon_csv(args)
    log_amazon_stats(amazon_items, amazon_orders, amazon_refunds)

    mint_client = None

    def close_mint_client():
        if mint_client:
            mint_client.close()

    atexit.register(close_mint_client)

    if args.pickled_epoch:
        mint_transactions, mint_category_name_to_id = (
            get_trans_and_categories_from_pickle(args.pickled_epoch))
    else:
        mint_client = get_mint_client(args)

        # Only get transactions as new as the oldest Amazon order.
        oldest_trans_date = min([o['Order Date'] for o in amazon_orders])
        if amazon_refunds:
            oldest_trans_date = min(
                oldest_trans_date,
                min([o['Order Date'] for o in amazon_refunds]))
        mint_transactions, mint_category_name_to_id = (
            get_trans_and_categories_from_mint(mint_client, oldest_trans_date))
        epoch = int(time.time())
        dump_trans_and_categories(
            mint_transactions, mint_category_name_to_id, epoch)

    def get_prefix(is_debit):
        return (args.description_prefix if is_debit
                else args.description_return_prefix)

    logger.info('\nMatching Amazon pruchases to Mint transactions.')
    stats = Counter()
    orig_trans_to_tagged = tag_transactions(
        amazon_items, amazon_orders, amazon_refunds,
        mint_transactions, not args.no_itemize, get_prefix, stats)

    filtered = sanity_check_and_filter_tags(
        orig_trans_to_tagged, mint_category_name_to_id, get_prefix,
        args, stats)

    log_processing_stats(stats, get_prefix)

    if not filtered:
        logger.info(
            'All done; no new tags to be updated at this point in time!.')
        exit(0)

    if args.dry_run:
        logger.info('Dry run. Following are proposed changes:')
        print_dry_run(filtered)
    else:
        # Ensure we have a Mint client.
        if not mint_client:
            mint_client = get_mint_client(args)

        write_tags_to_mint(filtered, mint_client)


if __name__ == '__main__':
    main()
