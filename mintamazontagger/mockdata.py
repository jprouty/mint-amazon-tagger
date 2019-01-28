from collections import OrderedDict

from mintamazontagger import amazon
from mintamazontagger import mint


def transaction(*args, **kwargs):
    return mint.Transaction(transaction_json(*args, **kwargs))


def order(*args, **kwargs):
    return amazon.Order(order_dict(*args, **kwargs))


def item(*args, **kwargs):
    return amazon.Item(item_dict(*args, **kwargs))


def refund(*args, **kwargs):
    return amazon.Refund(refund_dict(*args, **kwargs))


def transaction_json(
        amount='$11.95',
        is_debit=True,
        category='Personal Care',
        date='2/28/14',
        merchant='Amazon',
        original_description='AMAZON MKTPLACE PMTS',
        id=975256256,
        pid=None,
        note='Great note here'):
    trans = {
        'account': 'Amazon Visa',
        'amount': amount,
        'category': category,
        'categoryId': 4,
        'date': date,
        'fi': 'Chase Credit Card',
        'hasAttachments': False,
        'id': id,
        'isAfterFiCreationTime': True,
        'isCheck': False,
        'isChild': False,
        'isDebit': is_debit,
        'isDuplicate': False,
        'isEdited': True,
        'isFirstDate': True,
        'isLinkedToRule': False,
        'isMatched': False,
        'isPending': False,
        'isPercent': False,
        'isSpending': True,
        'isTransfer': False,
        'labels': [],
        'manualType': 0,
        'mcategory': 'Restaurants',
        'merchant': merchant,
        'mmerchant': 'Amazon Marketplace',
        'note': note,
        'number_matched_by_rule': -1,
        'odate': date,
        'omerchant': original_description,
        'ruleCategory': '',
        'ruleCategoryId': 0,
        'ruleMerchant': '',
        'txnType': 0,
        'userCategoryId': 4,
    }
    if pid:
        trans['isChild'] = True
        trans['pid'] = pid
    return trans


def order_dict(
        subtotal='$10.90',
        shipping_charge='$0.00',
        tax_charged='$1.05',
        total_charged='$11.95',
        tax_before_promotions='$1.05',
        total_promotions='$0.00',
        tracking='AMZN(ABC123)',
        order_status='Shipped',
        order_id='123-3211232-7655671',
        order_date='02/26/14',
        shipment_date='02/28/14',
        payment_type='Great Credit Card'):
    return OrderedDict([
        ('Order Date', order_date),
        ('Order ID', order_id),
        ('Payment Instrument Type', payment_type),
        ('Website', 'Amazon.com'),
        ('Purchase Order Number', ''),
        ('Ordering Customer Email', 'yup@aol.com'),
        ('Shipment Date', shipment_date),
        ('Shipping Address Name', 'Some Great Buyer'),
        ('Shipping Address Street 1', 'The best city'),
        ('Shipping Address Street 2', 'But can be rainy, sometimes'),
        ('Shipping Address City', 'SEATTLE'),
        ('Shipping Address State', 'WA'),
        ('Shipping Address Zip', '98101-1001'),
        ('Order Status', order_status),
        ('Carrier Name & Tracking Number', tracking),
        ('Subtotal', subtotal),
        ('Shipping Charge', shipping_charge),
        ('Tax Before Promotions', tax_before_promotions),
        ('Total Promotions', total_promotions),
        ('Tax Charged', tax_charged),
        ('Total Charged', total_charged),
        ('Buyer Name', 'Some Great Buyer'),
        ('Group Name', 'Optional Group'),
    ])


def item_dict(
        title='Duracell AAs',
        item_subtotal='$10.90',
        item_subtotal_tax='$1.05',
        item_total='$11.95',
        purchase_price_per_unit='$5.45',
        tracking='AMZN(ABC123)',
        quantity=2,
        order_status='Shipped',
        order_id='123-3211232-7655671',
        order_date='02/26/14',
        shipment_date='02/28/14',
        payment_type='Great Credit Card'):
    return OrderedDict([
        ('Order Date', order_date),
        ('Order ID', order_id),
        ('Title', title),
        ('Category', 'Misc.'),
        ('ASIN/ISBN', 'B00009V2QX'),
        ('UNSPSC Code', '26111700'),
        ('Website', 'Amazon.com'),
        ('Release Date', '04/15/10'),
        ('Condition', 'new'),
        ('Seller', 'Todays Concept'),
        ('Seller Credentials', ''),
        ('List Price Per Unit', purchase_price_per_unit),
        ('Purchase Price Per Unit', purchase_price_per_unit),
        ('Quantity', str(quantity)),
        ('Payment Instrument Type', payment_type),
        ('Purchase Order Number', ''),
        ('PO Line Number', ''),
        ('Ordering Customer Email', 'yup@aol.com'),
        ('Shipment Date', shipment_date),
        ('Shipping Address Name', 'Some Great Buyer'),
        ('Shipping Address Street 1', 'The best city'),
        ('Shipping Address Street 2', 'But can be rainy, sometimes'),
        ('Shipping Address City', 'SEATTLE'),
        ('Shipping Address State', 'WA'),
        ('Shipping Address Zip', '98101-1001'),
        ('Order Status', order_status),
        ('Carrier Name & Tracking Number', tracking),
        ('Item Subtotal', item_subtotal),
        ('Item Subtotal Tax', item_subtotal_tax),
        ('Item Total', item_total),
        ('Tax Exemption Applied', ''),
        ('Tax Exemption Type', ''),
        ('Exemption Opt-Out', ''),
        ('Buyer Name', 'Some Great Buyer'),
        ('Currency', 'USD'),
        ('Group Name', 'Optional Group'),
    ])


def refund_dict(
        title='Duracell Procell AA 24 Pack PC1500BKD09',
        refund_amount='$10.90',
        refund_tax_amount='$1.05',
        tracking='AMZN(ABC123)',
        status='Shipped',
        quantity=2,
        order_id='123-3211232-7655671',
        order_date='02/26/14',
        refund_date='03/16/14'):
    return OrderedDict([
        ('Order Date', order_date),
        ('Order ID', order_id),
        ('Title', title),
        ('Category', 'Apparel'),
        ('ASIN/ISBN', 'B0174V9GZW'),
        ('Website', 'Amazon.com'),
        ('Purchase Order Number', ''),
        ('Refund Date', refund_date),
        ('Refund Condition', 'Completed'),
        ('Refund Amount', refund_amount),
        ('Refund Tax Amount', refund_tax_amount),
        ('Tax Exemption Applied', ''),
        ('Refund Reason', 'Customer Return'),
        ('Quantity', quantity),
        ('Seller', 'Customonaco'),
        ('Seller Credentials', ''),
        ('Buyer Name', 'Some Great Buyer'),
        ('Group Name', 'Optional Group'),
    ])
