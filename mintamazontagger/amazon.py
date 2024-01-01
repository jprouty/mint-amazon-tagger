from copy import deepcopy
import csv
from datetime import datetime, timezone
from dateutil import parser
import io
import logging
from pprint import pformat
import re
import string

from mintamazontagger import category
from mintamazontagger.currency import float_usd_to_micro_usd
from mintamazontagger.currency import micro_usd_nearly_equal
from mintamazontagger.currency import micro_usd_to_usd_string
from mintamazontagger.currency import parse_usd_as_micro_usd, round_micro_usd_to_cent
from mintamazontagger.currency import CENT_MICRO_USD, MICRO_USD_EPS
from mintamazontagger.mint import truncate_title
from mintamazontagger.my_progress import no_progress_factory

logger = logging.getLogger(__name__)

PRINTABLE = set(string.printable)


ORDER_HISTORY_CSV_PATTERN = re.compile(
    r'Retail.OrderHistory.\d+/Retail.OrderHistory.\d+.csv')

def is_order_history_csv(zip_file_name):
    return bool(ORDER_HISTORY_CSV_PATTERN.match(zip_file_name))


def rm_leading_qty(item_title):
    """Removes the '2x Item Name' from the front of an item title."""
    return re.sub(r'^\d+x ', '', item_title)


def get_title(amzn_obj, target_length):
    # Also works for a Refund record.
    qty = amzn_obj.quantity
    base_str = None
    if qty > 1:
        base_str = str(qty) + 'x'
    # Remove non-ASCII characters from the title.
    clean_title = ''.join(filter(lambda x: x in PRINTABLE, amzn_obj.product_name))
    return truncate_title(clean_title, target_length, base_str)

CURRENCY_FIELD_NAMES = set([
    'Unit Price',
    'Unit Price Tax',
    'Shipping Charge',
    'Total Discounts',
    'Total Owed',
    'Shipment Item Subtotal',
    'Shipment Item Subtotal Tax',
])

DATE_FIELD_NAMES = set([
    'Order Date',
    'Ship Date',
])

# TODO: Fix quoting issue with Website".
RENAME_FIELD_NAMES = {
    'Carrier Name & Tracking Number': 'tracking',
    'Website"': 'website',
}

MULTI_SPLIT_BY_AND = set([
    'Order Date',
    'Ship Date',
    'tracking',
    'Payment Instrument Type'
])

def parse_from_csv_common(
        cls,
        csv_file,
        progress_label='Parse from csv',
        progress_factory=no_progress_factory):
    # contents = csv_file.read().decode()
    contents = csv_file.read().decode('utf-8')
    # Strip a leading FEFF if present.
    if contents[0:1] == '\ufeff':
        contents = contents[2:]

    num_records = sum(1 for c in contents if c == '\n') - 1
    result = []
    if not num_records:
        return result
    
    progress = progress_factory(progress_label, num_records)
    reader = csv.DictReader(io.StringIO(contents))
    for raw_dict in reader:
        result.append(cls(raw_dict))
        progress.next()
    progress.finish()
    return result


def pythonify_amazon_dict(raw_dict):
    keys = set(raw_dict.keys())

    if 'Quantity' in keys:
        raw_dict['Quantity'] = int(raw_dict['Quantity'])

    # Convert to microdollar ints
    for ck in keys & CURRENCY_FIELD_NAMES:
        raw_dict[ck] = parse_usd_as_micro_usd(raw_dict[ck])

    # Split fields with multiples by " and ":
    for split_key in keys & MULTI_SPLIT_BY_AND:
        raw_dict[split_key] = raw_dict[split_key].split(" and ")

    # Convert to datetime.date
    for dk in keys & DATE_FIELD_NAMES:
        raw_dict[dk] = [parse_amazon_date(d) for d in raw_dict[dk]]

    # Rename long or unpythonic names:
    for old_key in keys & RENAME_FIELD_NAMES.keys():
        new_key = RENAME_FIELD_NAMES[old_key]
        raw_dict[new_key] = raw_dict[old_key]
        del raw_dict[old_key]

    return dict([
        (k.lower().replace(' ', '_').replace('/', '_'), v)
        for k, v in raw_dict.items()
    ])


# TODO: Consider if we want to retain the time.
def parse_amazon_date(date_str):
    if not date_str or date_str == 'Not Available':
        return None
    return parser.parse(date_str)
    # return datetime.strptime(date_str, '%Y-%m-%dT%H:%M:%S.Z').date()


def get_invoice_url(order_id):
    return (
        'https://www.amazon.com/gp/css/summary/print.html?ie=UTF8&'
        f'orderID={order_id}')


# ORDER_MERGE_FIELDS = {
#     'original_shipping_charge',
#     'shipping_charge',
#     'subtotal',
#     'tax_before_promotions',
#     'tax_charged',
#     'total_charged',
#     'total_discounts',
# }


def datetime_list_to_dates_str(dates):
    return ', '.join([d.strftime("%Y-%m-%d") for d in dates])


class Charge:
    """A Charge represents a set of items corresponding to one payment.

    A Charge can have (TODO: validate each):
    - One or more items with one or more per quantity each
    - One or more tracking numbers
    - One or more shipment dates/times
    - One or more payment instruments, including partial or complete usage of gift cards.
    
    A Charge cannot have:
    - Items from different order IDs
    """
    matched = False
    trans_id = None
    items = []

    def __init__(self, items):
        self.items = items

    # def subtotal(self):
    #     return sum([i.amount_charged for i in self.items])

    # @staticmethod
    # def sum_subtotals(charges):
    #     return sum([o.subtotal for o in charges])

    def has_hidden_shipping_fee(self):
        # Colorado - https://tax.colorado.gov/retail-delivery-fee
        # "Effective July 1, 2022, Colorado imposes a retail delivery fee on
        # all deliveries by motor vehicle to a location in Colorado with at
        # least one item of tangible personal property subject to state sales
        # or use tax."
        # Rate July 2022 to June 2023: $0.27
        # This is not the case as of 8/31/2022 for Amazon Order Reports.
        # "Retailers that make retail deliveries must show the total of the
        # fees on the receipt or invoice as one item called “retail delivery
        # fees”."
        # TODO: Improve the ' CO ' Matching, consider a regex w/ a zip code element.
        ship_dates = self.ship_dates()
        return (
            ' CO ' in self.ship_address() 
            and self.tax() > 0
            and ship_dates and max(ship_dates) >= datetime(2022, 7, 1, tzinfo=timezone.utc))

    def hidden_shipping_fee(self):
        return float_usd_to_micro_usd(0.27)

    def hidden_shipping_fee_note(self):
        return 'CO Retail Delivery Fee'

    def total_by_items(self):
        return (
            Item.sum_totals(self.items)
            + (self.hidden_shipping_fee() if self.has_hidden_shipping_fee() else 0)
            + self.shipping_charge() + self.total_discounts())

    # def total_by_subtotals(self):
    #     return (
    #         self.subtotal + self.tax_charged
    #         + self.shipping_charge + self.total_discounts())

    def set_items(self, items, assert_unmatched=False):
        # Make a new list (to prevent retaining the given list).
        self.items = []
        self.items.extend(items)
        self.items_matched = True
        for i in items:
            if assert_unmatched:
                assert not i.matched
            i.matched = True
            i.charge = self
    
    def total_quantity(self):
        return sum([i.quantity for i in self.items])
    
    def order_id(self):
        return self.items[0].order_id

    def order_status(self):
        return self.items[0].order_status
    
    def ship_status(self):
        return self.items[0].shipment_status

    def website(self):
        return self.items[0].website

    def ship_address(self):
        return self.items[0].shipping_address
    
    def payment_instrument_types(self):
        return set([pit for i in self.items for pit in i.payment_instrument_type])
    
    def order_dates(self):
        return [date for items in self.items for date in items.order_date]
    
    def ship_dates(self):
        return [date for items in self.items for date in items.ship_date]
    
    def unique_order_dates(self):
        return list(set([d.date() for d in self.order_dates()]))

    def unique_ship_dates(self):
        return list(set([d.date() for d in self.ship_dates()]))

    def subtotal(self):
        return Item.sum_subtotals(self.items)
    
    def tax(self):
        return Item.sum_subtotals_tax(self.items)
    
    def total(self):
        """This should be = subtotal + tax."""
        return Item.sum_totals(self.items)
    
    def shipping_charge(self):
        return sum([i.shipping_charge for i in self.items])
    
    def total_discounts(self):
        return sum([i.total_discounts for i in self.items])
    
    def total_owed(self):
        """This should be = total + shipping_charge + total_discounts."""
        return sum([i.total_owed for i in self.items])
    
    def tracking_numbers(self):
        return list(set([items.tracking for items in self.items]))
    
    def transact_date(self):
        """The latest ship date in local time zone."""
        dates = [d for i in self.items if i.ship_date for d in i.ship_date]
        if not dates:
            return None
        # Use the local timezone (report has them in UTC).
        # UTC will cause matching to be incorrect.
        return max(dates).astimezone().date()
    
        # if self.items[0].ship_date and self.items[0].ship_date[0]:
        #     
        #     return self.items[0].ship_date[0].astimezone().date()

    def transact_amount(self):
        if self.has_hidden_shipping_fee():
            return -round_micro_usd_to_cent(self.total_owed() + self.hidden_shipping_fee())
        return -self.total_owed()

    def match(self, trans):
        self.matched = True
        self.trans_id = trans.id
        # if self.order_id() in ('112-9523119-2065026', '113-7797306-4423467', '112-5028447-9842607'):
        #     print('A match made in MAT')
        #     print(self)
        #     print(trans)

    def get_notes(self):
        note = (f'Amazon order id: {self.order_id()}\n'
            f'Order date: {datetime_list_to_dates_str(self.unique_order_dates())}\n'
            f'Ship date: {datetime_list_to_dates_str(self.unique_ship_dates())}\n'
            f'Tracking: {", ".join(self.tracking_numbers())}\n'
            f'Invoice url: {get_invoice_url(self.order_id())}')
        # Notes max out at 1000 as of 2023/12/31. If at or above the limit, use a simplified note:
        if len(note) >= 1000:
            logger.warn('Truncating note for Amazon Charge due to excessive length')
            note = (f'Amazon order id: {self.order_id()}\n'
                    f'Invoice url: {get_invoice_url(self.order_id())}')
        return note

    def attribute_subtotal_diff_to_misc_charge(self):
        """Sometimes gift wrapping or other misc charge is captured within 'total_owed' for an item but it doesn't belong there."""
        diff = self.total_owed() - self.total_by_items()
        if diff < MICRO_USD_EPS:
            return False

        adjustments = 0
        for i in self.items:
            item_diff = i.total_owed - i.total_owed_by_parts()
            if item_diff > MICRO_USD_EPS:
                i.total_owed -= item_diff

                adjustment = deepcopy(self.items[0])
                adjustment.product_name = 'Misc Charge (Gift wrap, etc)'
                adjustment.category = 'Shopping'
                adjustment.quantity = 1
                adjustment.shipping_charge = 0
                adjustment.total_discounts = 0

                adjustment.unit_price = item_diff
                adjustment.shipment_item_subtotal = item_diff
                adjustment.total_owed = item_diff

                adjustment.unit_price_tax = 0
                adjustment.shipment_item_subtotal_tax = 0
                adjustment.unit_price_tax = 0

                self.items.append(adjustment)
                adjustments += 1

        return adjustments > 0

    def attribute_itemized_diff_to_shipping_error(self):
        # Shipping is sometimes wrong. Remove shipping off of items if it is the only thing preventing a clean reconcile.
        if not self.shipping_charge():
            return False

        diff = self.total_by_items() - self.total_owed()
        if diff < MICRO_USD_EPS:
            return False

        # Find an item with a non-zero shipping charge and add on the tax.
        adjustments = 0
        for i in self.items:
            item_diff = i.total_owed_by_parts() - i.total_owed
            if micro_usd_nearly_equal(item_diff, i.shipping_charge):
                i.shipping_charge = 0
                adjustments += 1
        return adjustments > 0

    def attribute_itemized_diff_to_item_fractional_tax(self):
        """Correct for a slight mismatch when multiple quantities cause per-item taxes to not add up."""
        if self.total_quantity() < 2:
            return False
        
        itemized_diff = self.total_owed() - self.total_by_items()
        if abs(itemized_diff) < MICRO_USD_EPS:
            return False
        
        # Only correct for a maximum amount of rounding errors up to the quantity of items in the charge.
        if itemized_diff < CENT_MICRO_USD * self.total_quantity():
            per_item_tax_adjustment = itemized_diff / self.total_quantity()
            for i in self.items:
                i.unit_price_tax += per_item_tax_adjustment
            return True
        return False

    def to_mint_transactions(self,
                             t,
                             skip_free_shipping=False):
        new_transactions = []

        # More expensive items are always more interesting when it comes to
        # budgeting, so show those first (for both itemized and concatted).
        items = sorted(
            self.items, key=lambda item: item.unit_price, reverse=True)

        # Itemize line-items:
        for i in items:
            # new_cat = category.get_mint_category_from_unspsc(i.unspsc_code)
            item = t.split(
                amount=-i.total(),
                category_name=t.category.name,
                description=i.get_title(88),
                notes=self.get_notes())
            new_transactions.append(item)

        if self.has_hidden_shipping_fee():
            ship_fee = t.split(
                amount=-self.hidden_shipping_fee(),
                category_name='Shipping',
                description=self.hidden_shipping_fee_note(),
                notes=self.get_notes())
            new_transactions.append(ship_fee)

        # Itemize the shipping cost, if any.
        is_free_shipping = (
            self.shipping_charge()
            and self.total_discounts()
            and micro_usd_nearly_equal(
                self.total_discounts(), self.shipping_charge()))

        if is_free_shipping and skip_free_shipping:
            return new_transactions

        if self.shipping_charge():
            ship = t.split(
                amount=-self.shipping_charge(),
                category_name='Shipping',
                description='Shipping',
                notes=self.get_notes())
            new_transactions.append(ship)

        # All promotion(s) as one line-item.
        if self.total_discounts():
            # If there was a promo that matches the shipping cost, it's nearly
            # certainly a Free One-day/same-day/etc promo. In this case,
            # categorize the promo instead as 'Shipping', which will cancel out
            # in Mint trends.

            # Note: Since the move to Amazon Request My Data, same/next day
            # shipping that was e.g. $2.99 and then comp'd is no longer
            # included in these reports (discounts and shipping are both zero'd
            # out).  
            cat = ('Shipping' if is_free_shipping else
                   category.DEFAULT_MINT_CATEGORY)
            promo = t.split(
                amount=-self.total_discounts(),
                category_name=cat,
                description='Promotion(s)',
                notes=self.get_notes())
            new_transactions.append(promo)

        return new_transactions

    @classmethod
    def merge(cls, charges):
        if len(charges) == 1:
            return charges[0]
            # result.set_items(Item.merge(result.items))
            # return result

        return Charge([i for c in charges for i in c.items])
        # result = deepcopy(charges[0])
        # result.set_items(Item.merge([i for o in charges for i in o.items]))
        # # for key in ORDER_MERGE_FIELDS:
        # #     result.__dict__[key] = sum([o.__dict__[key] for o in charges])
        # return result

    def __repr__(self):
        return (
            f'Charge ({self.order_id()}): {self.ship_dates() or self.order_dates()}'
            f' Total {micro_usd_to_usd_string(self.total_owed())}\t'
            f' Total by part {micro_usd_to_usd_string(self.total_by_items())}\t'
            f'Subtotal {micro_usd_to_usd_string(self.subtotal())}\t'
            f'Tax {micro_usd_to_usd_string(self.tax())}\t'
            f'Promo {micro_usd_to_usd_string(self.total_discounts())}\t'
            f'Ship {micro_usd_to_usd_string(self.shipping_charge())}\t'
            f'Items: \n{pformat(self.items)}')


class Item:
    """A charge comprises of one or more Items with one or more quantity.
    
    The general formula for an item is:
    REVISE!!!
    shipment_item_subtotal = unit_price * quantity + other items in same charge
    shipment_item_tax = unit_price_tax * quantity + other items in same charge
    shipment_item_total = shipment_item_subtotal + shipment_item_tax
    total_owed = shipment_item_total + shipping_charge + total_discounts
    """

    matched = False
    order = None

    def __init__(self, raw_dict):
        self.__dict__.update(pythonify_amazon_dict(raw_dict))

    @classmethod
    def parse_from_csv(cls, csv_file, progress_factory=no_progress_factory):
        return parse_from_csv_common(
            cls, csv_file, 'Parsing Amazon Items', progress_factory)

    @staticmethod
    def sum_subtotals(items):
        return sum([i.subtotal() for i in items])
    
    @staticmethod
    def sum_subtotals_tax(items):
        return sum([i.subtotal_tax() for i in items])

    @staticmethod
    def sum_totals(items):
        return sum([i.total() for i in items])

    def subtotal(self):
        return self.quantity * self.unit_price

    def subtotal_tax(self):
        return self.quantity * self.unit_price_tax

    def total(self):
        """Prior to shipping_charge and total_discounts."""
        return self.subtotal() + self.subtotal_tax()

    def total_owed_by_parts(self):
        """Prior to shipping_charge and total_discounts."""
        return self.total() + self.total_discounts + self.shipping_charge

    # def adjust_unit_tax_based_on_total_owed(self):
    #     """
    #     Returns true if per unit taxes are fractionally adjusted to align total_owed with per unit prices.

    #     This happens when quantity is greater than one and is illustrated as:
    #       total_owed != quantity * (unit_price + unit_price_tax) + total_discounts + shipping_charge
    #     unit_price_tax is rounded, which causes a mismatch when devining total_owed from per-unit prices.
    #     """
    #     subtotal = self.total_owed - self.total_discounts - self.shipping_charge
    #     per_unit_subtotal = self.total()
    #     if subtotal == per_unit_subtotal:
    #         return False
        
    #     print(subtotal)
    #     print(per_unit_subtotal)
    #     print(self.__dict__)
    #     # exit()
    #     #     # TODO: Adjust tax to be fractional - more precise. Do this for all of the original charges (prior to merge). Adjust per-item amounts such that the total_owed always works out (Within reason - ie 2 cents?)
    #     # if i.total() + i.total_discounts != i.total_owed:
    #     #     print(i.total() + i.total_discounts)
    #     #     print(i.total_owed)
    #     #     print(i)

# 9990000 + 1010000

    def tax_rate(self):
        return round(self.unit_price_tax * 100.0 / self.unit_price, 1)

    def get_title(self, target_length=100):
        return get_title(self, target_length)

    def is_cancelled(self):
        return self.order_status == 'Cancelled'

    def __repr__(self):
        return (
            f'{self.quantity} of Item: '
            f'Order ID {self.order_id}\t'
            f'Status {self.order_status}\t'
            f'Ship Status {self.shipment_status}\t'
            f'Order Date {self.order_date}\t'
            f'Ship Date {self.ship_date}\t'
            f'Tracking {self.tracking}\t'
            f'Unit Price {micro_usd_to_usd_string(self.unit_price)}\t'
            f'Unit Tax {micro_usd_to_usd_string(self.unit_price_tax)}\t'
            f'Total Owed {micro_usd_to_usd_string(self.total_owed)}\t'
            f'Shipping Charge {micro_usd_to_usd_string(self.shipping_charge)}\t'
            f'Discounts {micro_usd_to_usd_string(self.total_discounts)}\t'
            f'{self.product_name}')


# class Refund:
#     matched = False
#     trans_id = None
#     is_refund = True

#     def __init__(self, raw_dict):
#         # Refunds are rad: AMZN doesn't total the tax + sub-total for you.
#         fields = pythonify_amazon_dict(raw_dict)
#         fields['total_refund_amount'] = (
#             fields['refund_amount'] + fields['refund_tax_amount'])
#         self.__dict__.update(fields)

#     @staticmethod
#     def sum_total_refunds(refunds):
#         return sum([r.total_refund_amount for r in refunds])

#     @classmethod
#     def parse_from_csv(cls, csv_file, progress_factory=no_progress_factory):
#         return parse_from_csv_common(
#             cls, csv_file, 'Parsing Amazon Refunds', progress_factory)

#     def match(self, trans):
#         self.matched = True
#         self.trans_id = trans.id

#     def transact_date(self):
#         return self.refund_date

#     def transact_amount(self):
#         return self.total_refund_amount

#     def get_title(self, target_length=100):
#         return get_title(self, target_length)

#     def get_notes(self):
#         return (
#             f'Amazon refund for order id: {self.order_id}\n'
#             f'Buyer: {self.buyer_name}\n'
#             f'Order date: {self.order_date}\n'
#             f'Refund date: {self.refund_date}\n'
#             f'Refund reason: {self.refund_reason}\n'
#             f'Invoice url: {get_invoice_url(self.order_id)}')

#     def to_mint_transaction(self, t):
#         # Refunds have a positive amount.
#         result = t.split(
#             description=self.get_title(88),
#             category_name=category.DEFAULT_MINT_RETURN_CATEGORY,
#             amount=self.total_refund_amount,
#             notes=self.get_notes())
#         return result

#     @staticmethod
#     def merge(refunds):
#         """Collapses identical items by using quantity."""
#         if len(refunds) <= 1:
#             return refunds
#         unique_refund_items = defaultdict(list)
#         for r in refunds:
#             key = (
#                 f'{r.refund_date}-{r.refund_reason}-{r.title}-'
#                 f'{r.total_refund_amount}-{r.asin_isbn}')
#             unique_refund_items[key].append(r)
#         results = []
#         for same_items in unique_refund_items.values():
#             qty = sum([i.quantity for i in same_items])
#             if qty == 1:
#                 results.extend(same_items)
#                 continue

#             refund = same_items[0]
#             refund.quantity = qty
#             refund.total_refund_amount *= qty
#             refund.refund_amount *= qty
#             refund.refund_tax_amount *= qty

#             results.append(refund)
#         return results

#     def __repr__(self):
#         return (
#             f'{self.quantity} of Refund: '
#             f'Total {micro_usd_to_usd_string(self.total_refund_amount)}\t'
#             f'Subtotal {micro_usd_to_usd_string(self.refund_amount)}\t'
#             f'Tax {micro_usd_to_usd_string(self.refund_tax_amount)} '
#             f'{self.title}')
