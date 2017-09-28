#!/usr/bin/python

# This script takes Amazon "Order History Reports" and annotates your Mint
# transactions based on actual items in each purchase. It can handle orders
# that are split into multiple shipments/charges, and can even itemized each
# transaction for maximal control over categorization.

# First, you must generate and download your order history reports from:
# https://www.amazon.com/gp/b2b/reports

import argparse
from collections import defaultdict
import copy
import csv
import datetime
import logging
import pickle
import string
import time

import getpass
import keyring
import mint_api as mintapi # Temporary until mintapi is fixed upstream.
# import mintapi

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
    'Shipping Charge',
    'Subtotal',
    'Tax Charged',
    'Tax Before Promotions',
    'Total Charged',
    'Total Promotions',
])

AMAZON_DATE_FIELD_NAMES = set([
    'Order Date',
    'Shipment Date',
])

# 50 Micro dollars we'll consider equal (this allows for some
# division/multiplication rounding wiggle room).
MICRO_USD_EPS = 50
CENT_MICRO_USD = 10000

DOLLAR_EPS = 0.0001

MERCHANT_PREFIX = 'Amazon.com: '

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


def micro_usd_to_usd_float(micro_usd):
    return round_usd(micro_usd / 1000000.0)


def micro_usd_to_usd_string(micro_usd):
    return '{}${:}'.format(
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
    assert item['Purchase Price Per Unit'] * original_quantity == item['Item Subtotal']

    item['Item Subtotal'] = item['Purchase Price Per Unit'] * new_quantity
    item['Item Subtotal Tax'] = (item['Item Subtotal Tax'] / original_quantity) * new_quantity
    item['Item Total'] = item['Item Subtotal'] + item['Item Subtotal Tax']
    item['Quantity'] = new_quantity

    # Tag the item as being modified.
    item['ORIGINAL_QUANTITY_IN_ORDER'] = original_quantity


printable = set(string.printable)


def get_item_title(item, target_length):
    qty = item['Quantity']
    base_str = None
    if qty > 1:
        base_str = str(qty) + 'x'
    # Remove non-ASCII characters from the title.
    clean_title = filter(lambda x: x in printable, item['Title'])
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
    return 'Amazon order id: {0}\nOrder date: {1}\nShip date: {2}\nTracking: {3}'.format(
        order['Order ID'],
        order['Order Date'],
        order['Shipment Date'],
        order['Carrier Name & Tracking Number'])


def sum_amounts(trans):
    return sum([t['amount'] for t in trans])


def print_amazon_stats(items, orders):
    logger.info('Amazon Purchases Stats')
    logger.info('{} orders w/ {} items'.format(len(orders), len(items)))
    first_order_date = min([o['Order Date'] for o in orders])
    last_order_date = max([o['Order Date'] for o in orders])
    logger.info('Orders ranging from {} to {}'.format(first_order_date, last_order_date))

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


def tag_transactions(items, orders, trans, itemize):
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
        - trans: list of dicts. The user's Mint transactions.
        - itemize: bool. True will split a Mint transaction into per-item
          breakouts, and attempting to guess the appropriate category based on
          the Amazon item's category.

    Returns:
        A list of 2-tuples: [(existing trans, list[tagged trans, ..]), ...]
        Entries are only in the output if they have been successfully matched
        and validated with an Amazon order and properly itemized (or
        summarized).
    """
    # A multi-map from charged amount to orders.
    charged_to_orders = defaultdict(list)
    for o in orders:
        charged = o['Total Charged']
        charged_to_orders[charged].append(o)

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

    # Number of Mint transactions who's original description matches "Amazon"
    # (case in-sensitive).
    num_amazon_in_desc = 0

    # Number of pending Mint Transactions.
    num_pending = 0

    # Number of Amazon credits/refunds.
    num_credits = 0

    # Number of Mint transactions that are already itemized.
    num_already_itemized = 0

    # Number of Mint transactions that are already tagged.
    num_already_tagged = 0

    # Number of Mint transactions that have corresponding Amazon
    # reports. Typically Amazon credit card payments or other random
    # transactions drop off here.
    num_order_match = 0

    # Number of items needing to adjust the quantities to match the Mint
    # transaction correctly (partial order fulfillment).
    num_quanity_adjust = 0

    # Sometimes orders are partially fulfilled. When an item with a quantity
    # greater than 1 is split between two shipments/charges, it's unclear how
    # many items made it in that shipment.
    num_orders_need_combinatoric_adjustment = 0

    # Number of items that Amazon miscomputed the per-item tax subtotal on!
    # Really bizarre.
    num_items_tax_adjust = 0

    # Number of orders with a misc. mismatch in itemization total and order
    # total. This is almost always gift wrap.
    num_misc_charge = 0

    result = []

    for t in trans:
        # Skip t if the original description doesn't contain 'amazon'
        if 'amazon' not in t['omerchant'].lower():
            continue
        num_amazon_in_desc += 1

        if t['isPending']:
            num_pending += 1
            continue

        # TODO: Allow for reprocessing/tagging existing transactions.
        if t['isChild']:
            num_already_itemized += 1
            continue

        if t['merchant'].startswith(MERCHANT_PREFIX):
            num_already_tagged += 1
            continue

        # TODO: Handle refunds differently.
        if not t['isDebit']:
            num_credits += 1
            # Use the mint category: 'Returned Purchase'
            logger.debug('Skipping refund: {0}'.format(t))
            continue

        # Find an exact match in orders that matches the transaction cost.
        charge = t['amount']
        if charge not in charged_to_orders:
            logger.debug('Cannot find purchase for transaction: {0}'.format(t))
            # Look at additional matching strategies?
            continue

        matched_orders = charged_to_orders.get(charge)

        # Only consider it a match if the posted date (transaction date) is
        # within 3 days of the ship date of the order.
        closest_match = None
        closest_match_num_days = 365  # Large number
        for o in matched_orders:
            num_days = (t['odate'] - o['Shipment Date']).days
            # TODO: consider o even if it has a matched_transaction if this
            # transaction is closer.
            if (abs(num_days) < 4 and
                    abs(num_days) < closest_match_num_days and
                    'MATCHED_TRANSACTION' not in o):
                closest_match = o
                closest_match_num_days = abs(num_days)

        if not closest_match:
            logger.debug(
                'Cannot find viable order matching transaction {0}'.format(t))
            continue
        num_order_match += 1

        logger.debug(
            'Found a match: {0} for transaction: {1}'.format(
                closest_match, t))
        order = closest_match
        # Prevent future transactions matching up against this order.
        order['MATCHED_TRANSACTION'] = t

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
                continue
            items = order_id_to_items[order_id]
            if not items:
                continue

            item = None
            for i in items:
                if i['Purchase Price Per Unit'] == order['Subtotal']:
                    item = copy.deepcopy(i)
                    adjust_amazon_item_quantity(item, 1)
                    diff = order['Total Charged'] - item['Item Total']
                    if diff and abs(diff) < 10000:
                        item['Item Total'] += diff
                        item['Item Subtotal Tax'] += diff
                    num_quanity_adjust += 1
                    break

            if not item:
                num_orders_need_combinatoric_adjustment += 1
                continue

            items = [item]
        else:
            # Be sure to filter out other orders, as items from multiple orders
            # can indeed be packed/shipped together (but charged
            # independently).
            items = [i
                     for i in tracking_to_items[tracking]
                     if i['Order ID'] == order_id]

        if not items:
            continue

        for i in items:
            assert i['Order ID'] == order_id

        # More expensive items are always more interesting when it comes to
        # budgeting, so show those first (for both itemized and concatted).
        items = sorted(items, lambda x, y: cmp(y['Item Total'], x['Item Total']))

        new_transactions = []

        # Do a quick check to ensure all the item sub-totals add up to the
        # order sub-total.
        items_sum = sum([i['Item Subtotal'] for i in items])
        order_total = order['Subtotal']
        if abs(items_sum - order_total) > DOLLAR_EPS:
            # Uh oh, the sub-totals weren't equal. Try to fix, skip is not possible.
            if len(items) == 1:
                # If there's only one item, typically the quantity in this
                # charge/shipment was less than the total quantity ordered.
                # Copy this item as this case is highly like that the item
                # spans multiple shipments. Having the original item w/ the
                # original quantity is quite useful for the other half of the
                # order.
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
                    continue
            else:
                # TODO: Find the combination of items that add up to the
                # sub-total amount.
                num_orders_need_combinatoric_adjustment += 1
                continue

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
            ship_tax = order['Tax Charged'] - sum([i['Item Subtotal Tax'] for i in items])

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
        itemized_diff = t['amount'] - itemized_sum
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
                num_items_tax_adjust += 1

                # Not the optimal algorithm... but works.
                # Rounding forces the extremes to be corrected, but when
                # roughly equal, will take from the more expensive items (as
                # those are ordered first).
                tax_rate_per_item = [round(i['Item Subtotal Tax'] * 100.0 / i['Item Subtotal'], 1) for i in items]
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
                            items[min_idx]['Item Subtotal Tax'] * 100.0 / items[min_idx]['Item Subtotal'], 1)
                    else:
                        # Find the highest taxed item (by rate) and discount it a penny.
                        (max_idx, _) = max(enumerate(tax_rate_per_item), key=lambda x: x[1])
                        items[max_idx]['Item Subtotal Tax'] -= CENT_MICRO_USD
                        items[max_idx]['Item Total'] -= CENT_MICRO_USD
                        new_transactions[max_idx]['amount'] -= CENT_MICRO_USD
                        tax_diff += CENT_MICRO_USD
                        tax_rate_per_item[max_idx] = round(
                            items[max_idx]['Item Subtotal Tax'] * 100.0 / items[max_idx]['Item Subtotal'], 1)
            else:
                # The only examples seen at this point are due to gift wrap
                # fees. There must be other corner cases, so let's itemize with a
                # vague line item.
                num_misc_charge += 1

                adjustment = copy.deepcopy(t)
                adjustment['merchant'] = 'Misc Charge (Gift wrap, etc)'
                adjustment['category'] = category.DEFAULT_MINT_CATEGORY
                adjustment['amount'] = itemized_diff
                adjustment['isDebit'] = True
                adjustment['note'] = get_notes_header(order)

                new_transactions.append(adjustment)

        if itemize:
            # Prefix 'Amazon.com: ' to all itemized transactions for easy
            # keyword searching within Mint.
            for nt in new_transactions:
                nt['merchant'] = MERCHANT_PREFIX + nt['merchant']

            # Turns out the first entry is typically displayed last in
            # the Mint UI. Reverse everything for ideal readability.
            new_transactions = new_transactions[::-1]
        else:
            # When not itemizing, create a description by concating the
            # items. Store the full information in the transaction
            # notes. Category is untouched when there's more than one item
            # (this is why itemizing is better!).
            trun_len = (88 - 2 * len(items)) / len(items)
            title = MERCHANT_PREFIX + (', '.join(
                [truncate_title(nt['merchant'], trun_len)
                 for nt in new_transactions
                 if nt['merchant'] not in ('Promotion(s)', 'Shipping', 'Tax adjustment')]))
            notes = get_notes_header(order) + '\nItem(s):\n' + '\n'.join(
                [' - ' + nt['merchant']
                 for nt in new_transactions])

            summary_trans = copy.deepcopy(t)
            summary_trans['merchant'] = title
            if len(items) == 1:
                summary_trans['category'] = new_transactions['category']
            else:
                summary_trans['category'] = category.DEFAULT_MINT_CATEGORY
            summary_trans['note'] = notes
            new_transactions = [summary_trans]

        result.append((t, new_transactions))

    logger.info(
        'Transactions w/ "Amazon" in description: {}\n'
        'Transactions ignored: already tagged, has "{}" prefix: {}\n'
        'Transactions ignored: already tagged, is itemized/split expense: {}\n'
        'Transactions ignored: type is a credit: {}\n'
        'Transactions ignored: is pending: {}\n'
        'Transactions w/ matching order information: {}\n'
        'Transactions ignored: item quantity mismatch: {}\n'
        'Orders requiring itemization quantity tinkering: {}\n'
        'Orders w/ Incorrect tax itemization: {}\n'
        'Orders w/ Misc charges: {}\n'
        'Transactions successfully tagged/itemized: {}'.format(
            num_amazon_in_desc,
            MERCHANT_PREFIX,
            num_already_tagged,
            num_already_itemized,
            num_credits,
            num_pending,
            num_order_match,
            num_orders_need_combinatoric_adjustment,
            num_quanity_adjust,
            num_items_tax_adjust,
            num_misc_charge,
            len(result)))

    return result


def print_dry_run(orig_trans_to_tagged):
    logger.info('Dry run. Following are proposed changes:')

    num_requests = 0
    for (orig_trans, new_trans) in orig_trans_to_tagged:
        logger.info('Current:  {} \t {} \t {} \t ${}'.format(
            orig_trans['date'].strftime('%m/%d/%y'),
            orig_trans['merchant'],
            orig_trans['category'],
            micro_usd_to_usd_string(orig_trans['amount'])))

        if len(new_trans) == 1:
            trans = new_trans[0]
            logger.info('Proposed: {} \t {} \t {} \t ${} {}'.format(
                trans['date'].strftime('%m/%d/%y'),
                trans['merchant'],
                trans['category'],
                micro_usd_to_usd_string(trans['amount']),
                'with details in "Notes"' if orig_trans['note'] != trans['note'] else ''))
        else:
            for (i, trans) in enumerate(new_trans):
                logger.info('Proposed: {} \t {} \t {} \t ${}'.format(
                    trans['date'].strftime('%m/%d/%y'),
                    trans['merchant'],
                    trans['category'],
                    micro_usd_to_usd_string(trans['amount'])))


def write_tags_to_mint(orig_trans_to_tagged, mint_client):
    logger.info('Sending {} updates to Mint.'.format(len(orig_trans_to_tagged)))

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

            logger.debug('Sending a "modify" transaction request: {}'.format(modify_trans))
            response = mint_client.post(
                '{}{}'.format(
                    mintapi.api.MINT_ROOT_URL,
                    UPDATE_TRANS_ENDPOINT),
                data=modify_trans).text
            logger.debug('Received response: {}'.format(response))
            num_requests += 1
        else:
            # Split the existing transaction into many.
            itemized_split = {
                'txnId': '{}:0'.format(orig_trans['id']),
                'task': 'split',
                'data': '',  # Yup this is weird.
                'token': mint_client.token,
            }
            for (i, trans) in enumerate(new_trans):
                amount = micro_usd_to_usd_float(trans['amount'])
                itemized_split['amount{}'.format(i)] = amount
                itemized_split['percentAmount{}'.format(i)] = amount  # Yup. Weird!
                itemized_split['category{}'.format(i)] = trans['category']
                itemized_split['categoryId{}'.format(i)] = trans['categoryId']
                itemized_split['merchant{}'.format(i)] = trans['merchant']
                itemized_split['txnId{}'.format(i)] = 0  # Yup weird. Means new?

            logger.debug('Sending a "split" transaction request: {}'.format(itemized_split))
            response = mint_client.post(
                '{}{}'.format(
                    mintapi.api.MINT_ROOT_URL,
                    UPDATE_TRANS_ENDPOINT),
                data=itemized_split).text
            logger.debug('Received response: {}'.format(response))
            num_requests += 1

    end_time = time.time()
    dur_total_s = int(end_time - start_time)
    dur_s = dur_total_s % 60
    dur_m = (dur_total_s / 60) % 60
    dur_h = (dur_total_s / 60 / 60)
    dur = datetime.time(hour=dur_h, minute=dur_m, second=dur_s)
    logger.info('Sent {} updates to Mint in {}'.format(num_requests, dur))


def main():
    parser = argparse.ArgumentParser(
        description='Tag Mint transactions based on itemized Amazon history.')

    parser.add_argument(
        '--mint_email', default=None,
        help=('Mint e-mail address for login. If not provided here, will be '
              'prompted for user.'))
    parser.add_argument(
        '--mint_password', default=None,
        help=('Mint password for login. If not provided here, will be prompted '
              'for.'))

    parser.add_argument(
        'items_csv', type=argparse.FileType('r'),
        help='The "Items" Order History Report from Amazon')
    parser.add_argument(
        'orders_csv', type=argparse.FileType('r'),
        help='The "Orders and Shipments" Order History Report from Amazon')

    parser.add_argument(
        '--no_itemize', action='store_true',
        help=('P will split Mint transactions into individual items with '
              'attempted categorization.'))

    parser.add_argument(
        '--dry_run', action='store_true',
        help=('Do not modify Mint transaction; instead print the proposed '
              'changes to console.'))

    args = parser.parse_args()

    if args.dry_run:
        logger.info('Dry Run; no modifications being sent to Mint.')

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

    # Parse out Amazon reports (csv files). Do this first so any issues here
    # percolate before going to the cloudz for Mint.
    logger.info('Processing Amazon csv\'s.')
    amazon_items = pythonify_amazon_dict(
        list(csv.DictReader(args.items_csv)))
    amazon_orders = pythonify_amazon_dict(
        list(csv.DictReader(args.orders_csv)))

    # Sort everything for good measure/consistency/stable ordering.
    def order_date_cmp(x, y):
        return cmp(x['Order Date'], y['Order Date'])
    amazon_items = sorted(amazon_items, order_date_cmp)
    amazon_orders = sorted(amazon_orders, order_date_cmp)

    last_ius_session = keyring.get_password(
        KEYRING_SERVICE_NAME, '{}_ius_session'.format(email))
    last_thx_guid = keyring.get_password(
        KEYRING_SERVICE_NAME, '{}_thx_guid'.format(email))
    last_login_time = keyring.get_password(
        KEYRING_SERVICE_NAME, '{}_last_login'.format(email))

    # Reuse the stored ius_session and thx_guid if this script has run in the
    # last 15 minutes.
    if (last_ius_session and last_thx_guid and last_login_time and
            int(time.time()) - int(last_login_time) < 15 * 60) and False:
        logger.info('Using previous session tokens.')
        mint_client = mintapi.Mint.create(
            email, password, last_ius_session, last_thx_guid)
    else:
        # Requires chromedriver.
        logger.info('Logging in via chromedriver')
        mint_client = mintapi.Mint.create(email, password)

    logger.info('Login successful!')
    # On success, save off password, session tokens, and login time to keyring.
    keyring.set_password(KEYRING_SERVICE_NAME, email, password)
    keyring.set_password(
        KEYRING_SERVICE_NAME, '{}_ius_session'.format(email),
        mint_client.cookies['ius_session'])
    keyring.set_password(
        KEYRING_SERVICE_NAME, '{}_thx_guid'.format(email),
        mint_client.cookies['thx_guid'])
    keyring.set_password(
        KEYRING_SERVICE_NAME, '{}_last_login'.format(email),
        str(int(time.time())))

    # Create a map of Mint category name to category id.
    logger.info('Creating Mint Category Map.')
    mint_category_name_to_id = dict([
        (cat_dict['name'], cat_id)
        for (cat_id, cat_dict) in mint_client.get_categories().items()])

    # Only get transactions as new as the oldest Amazon order.
    oldest_order_date = min([o['Order Date'] for o in amazon_orders])
    # This may be broken for you until this is fixed and shipped:
    # https://github.com/mrooney/mintapi/pull/115
    start_date_str = oldest_order_date.strftime('%m/%d/%y')
    logger.info('Fetching all Mint transactions since {}.'.format(start_date_str))
    mint_transactions = pythonify_mint_dict(mint_client.get_transactions_json(
        start_date=start_date_str,
        include_investment=False,
        skip_duplicates=True))

    mint_backup_filename = 'Mint Transactions Backup {}.pickle'.format(
        int(time.time()))
    logger.info('Prior to modifying Mint Transactions, they have been backed '
                'up (picked) to: {}'.format(mint_backup_filename))
    with open(mint_backup_filename, 'w') as f:
        pickle.dump(mint_transactions, f)

    # Comment above and use the following when debugging tag_transactions:
    # mint_transactions = []
    # with open('Mint Transactions Backup 1505948039.pickle', 'r') as f:
    #     mint_transactions = pickle.load(f)

    print_amazon_stats(amazon_items, amazon_orders)

    logger.info('Matching Amazon pruchases to Mint transactions.')
    orig_trans_to_tagged = tag_transactions(
        amazon_items, amazon_orders, mint_transactions, not args.no_itemize)

    for (orig_trans, new_trans) in orig_trans_to_tagged:
        # Assert old trans amount == sum new trans amount
        assert abs(sum_amounts([orig_trans]) - sum_amounts(new_trans)) < MICRO_USD_EPS

        # Assert new transactions have valid categories and update the
        # categoryId based on name.
        for trans in new_trans:
            assert trans['category'] in mint_category_name_to_id
            trans['categoryId'] = mint_category_name_to_id[trans['category']]

        # Filter out unchanged entries to avoid duplicate work.
        # TODO: removing the filters earlier on and track splits
        # (re-constituting the original charge).

    if args.dry_run:
        print_dry_run(orig_trans_to_tagged)
    else:
        write_tags_to_mint(orig_trans_to_tagged, mint_client)


if __name__ == '__main__':
    main()
