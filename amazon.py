from collections import defaultdict, Counter
from copy import deepcopy
import csv
from datetime import datetime
from pprint import pformat, pprint
import string

from algorithm_u import algorithm_u
from currency import micro_usd_nearly_equal, parse_usd_as_micro_usd, MICRO_USD_EPS

PRINTABLE = set(string.printable)


def get_title(amzn_obj, target_length):
    # Also works for a Refund record.
    qty = amzn_obj.quantity
    base_str = None
    if qty > 1:
        base_str = str(qty) + 'x'
    # Remove non-ASCII characters from the title.
    clean_title = ''.join(filter(lambda x: x in PRINTABLE, amzn_obj.title))
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

CURRENCY_FIELD_NAMES = set([
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

DATE_FIELD_NAMES = set([
    'Order Date',
    'Refund Date',
    'Shipment Date',
])

RENAME_FIELD_NAMES = {
    'Carrier Name & Tracking Number': 'tracking',
}


def pythonify_amazon_dict(raw_dict):
    keys = set(raw_dict.keys())

    # Convert to microdollar ints
    for ck in keys & CURRENCY_FIELD_NAMES:
        raw_dict[ck] = parse_usd_as_micro_usd(raw_dict[ck])

    # Convert to datetime.date
    for dk in keys & DATE_FIELD_NAMES:
        raw_dict[dk] = parse_amazon_date(raw_dict[dk])

    # Rename long or unpythonic names:
    for old_key in keys & RENAME_FIELD_NAMES.keys():
        new_key = RENAME_FIELD_NAMES[old_key]
        raw_dict[new_key] = raw_dict[old_key]
        del raw_dict[old_key]

    if 'Quantity' in keys:
        raw_dict['Quantity'] = int(raw_dict['Quantity'])

    return dict([(k.lower().replace(' ', '_'), v) for k, v in raw_dict.items()])


def parse_amazon_date(date_str):
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, '%m/%d/%Y').date()
    except ValueError:
        return datetime.strptime(date_str, '%m/%d/%y').date()


def associate_items_with_orders(orders, items):
    # Remove items from cancelled orders.
    items = [i for i in items if not i.is_cancelled()]
    # Make more Items such that every item is quantity 1.
    items = [si for i in items for si in i.split_by_quantity()]

    items_by_oid = defaultdict(list)
    for i in items:
        items_by_oid[i.order_id].append(i)
    orders_by_oid = defaultdict(list)
    for o in orders:
        orders_by_oid[o.order_id].append(o)

    for oid, orders in orders_by_oid.items():
        oid_items = items_by_oid[oid]

        subtotal_equal = micro_usd_nearly_equal(
            Order.sum_subtotals(orders),
            Item.sum_subtotals(oid_items))
        assert subtotal_equal
        
        if len(orders) == 1:
            orders[0].set_items(oid_items)
            continue

        # First try to divy up the items by tracking.
        items_by_tracking = defaultdict(list)
        for i in oid_items:
            items_by_tracking[i.tracking].append(i)

        # It is never the case that multiple orders with the same order id will
        # have the same tracking number.
        for order in orders:
            items = items_by_tracking[order.tracking]
            if micro_usd_nearly_equal(
                    Item.sum_subtotals(items),
                    order.subtotal):
                # A perfect fit.
                order.set_items(items)
                # Remove the selected items.
                oid_items = [i for i in oid_items if i not in items]
        # Remove orders that have items.
        orders = [o for o in orders if not o.items]
        if not orders and not oid_items:
            continue

        orders = sorted(orders, key=lambda o: o.subtotal)
        
        # Partition the remaining items into every possible arrangement and
        # validate against the remaining orders.
        for item_groupings in algorithm_u(oid_items, len(orders)):
            subtotals_with_groupings = sorted(
                [(Item.sum_subtotals(items), items) for items in item_groupings],
                key=lambda g: g[0])
            if all([micro_usd_nearly_equal(
                    subtotals_with_groupings[i][0],
                    orders[i].subtotal) for i in range(len(orders))]):
                for idx, order in enumerate(orders):
                    order.set_items(subtotals_with_groupings[idx][1])
                break        


class Order:
    matched = False
    trans_id = None
    items = []
    
    def __init__(self, raw_dict):
        self.__dict__.update(pythonify_amazon_dict(raw_dict))
        
    @classmethod
    def parse_from_csv(cls, csv_file):
        return [cls(raw_dict) for raw_dict in csv.DictReader(csv_file)]

    @staticmethod
    def sum_subtotals(orders):
        return sum([o.subtotal for o in orders])

    def transact_date(self):
        return self.shipment_date

    def transact_amount(self):
        return self.total_charged
    
    def match(self, trans):
        self.matched = True
        self.trans_id = trans.id
    
    def set_items(self, items):
        self.items = items

    def get_note(self):
        return (
            'Amazon order id: {}\n'
            'Order date: {}\n'
            'Ship date: {}\n'
            'Tracking: {}').format(
                self.order_id,
                self.order_date,
                self.shipment_date,
                self.tracking)
    
    def __repr__(self):
        return pformat(self.__dict__)


class Item:
    is_matched = False
    order = None

    def __init__(self, raw_dict):
        self.__dict__.update(pythonify_amazon_dict(raw_dict))
        
    @classmethod
    def parse_from_csv(cls, csv_file):
        return [cls(raw_dict) for raw_dict in csv.DictReader(csv_file)]
    
    @staticmethod
    def sum_subtotals(items):
        return sum([i.item_subtotal for i in items])

    def get_title(self, target_length=100):
        return get_title(self, target_length)

    def is_cancelled(self):
        return self.order_status == 'Cancelled'
   
    def set_quantity(self, new_quantity):
        """Sets the quantity of this item and updates all prices."""
        original_quantity = self.quantity

        assert new_quantity > 0
        assert new_quantity <= original_quantity
        subtotal_equal = micro_usd_nearly_equal(
            self.purchase_price_per_unit * original_quantity,
            self.item_subtotal)
        assert subtotal_equal < MICRO_USD_EPS

        self.item_subtotal = self.purchase_price_per_unit * new_quantity
        self.item_subtotal_tax = (
            self.item_subtotal_tax / original_quantity) * new_quantity
        self.item_total = self.item_subtotal + self.item_subtotal_tax
        self.quantity = new_quantity

    def split_by_quantity(self):
        """Splits this item into 'quantity' items."""
        if self.quantity == 1:
            return [self]
        orig_qty = self.quantity
        self.set_quantity(1)
        return [deepcopy(self) for i in range(orig_qty)]
    
    def __repr__(self):
        return pformat(self.__dict__)
    

class Refund:
    matched = False
    trans_id = None

    def __init__(self, raw_dict):
        # Refunds are rad: AMZN doesn't total the tax + sub-total for you.
        fields = pythonify_amazon_dict(raw_dict)
        fields['total_refund_amount'] = (
            fields['refund_amount'] + fields['refund_tax_amount'])
        self.__dict__.update(fields)
        
    @classmethod
    def parse_from_csv(cls, csv_file):
        return [cls(raw_dict) for raw_dict in csv.DictReader(csv_file)]

    def match(self, trans):
        self.matched = True
        self.trans_id = trans.id

    def transact_date(self):
        return self.refund_date

    def transact_amount(self):
        return -self.total_refund_amount
    
    def get_title(self, target_length=100):
        return get_title(self, target_length)

    def get_note(self):
        return (
            'Amazon refund for order id: {}\n'
            'Order date: {}\n'
            'Refund date: {}\n'
            'Refund reason: {}').format(
                self.order_id,
                self.order_date,
                self.refund_date,
                self.refund_reason)

    @staticmethod
    def merge_refunds(refunds):
        """Collapses identical items by using quantity."""
        if len(refunds) <= 1:
            return refunds
        unique_refund_items = defaultdict(list)
        for r in refunds:
            key = '{}-{}-{}-{}-{}-{}'.format(
                r['Refund Date'],
                r['Refund Reason'],
                r['Title'],
                r['Total Refund Amount'],
                r['ASIN/ISBN'],
                r['Quantity'])
            unique_refund_items[key].append(r)
        results = []
        for same_items in unique_refund_items.values():
            qty = len(same_items)
            if qty == 1:
                results.extend(same_items)
                continue

            refund = same_items[0]
            refund.quantity = qty
            refund.total_refund_amount *= qty
            refund.refund_amount *= qty
            refund.refund_tax_amount *= qty
            
            results.append(refund)
        return results

    def __repr__(self):
        return pformat(self.__dict__)

