from datetime import date
import unittest

from mintamazontagger import amazon
from mintamazontagger.amazon import Item, Order, Refund
from mintamazontagger.mockdata import item, order, refund, transaction


class HelperMethods(unittest.TestCase):
    def test_parse_amazon_date(self):
        self.assertEqual(
            amazon.parse_amazon_date('10/8/10'),
            date(2010, 10, 8))
        self.assertEqual(
            amazon.parse_amazon_date('1/23/10'),
            date(2010, 1, 23))
        self.assertEqual(
            amazon.parse_amazon_date('6/1/01'),
            date(2001, 6, 1))

        self.assertEqual(
            amazon.parse_amazon_date('07/21/2010'),
            date(2010, 7, 21))
        self.assertEqual(
            amazon.parse_amazon_date('1/23/1989'),
            date(1989, 1, 23))

    def test_associate_items_with_orders_none_match(self):
        i1 = item(order_id='1', item_subtotal='$100.00')
        i2 = item(order_id='2')
        i3 = item(order_id='3')
        i4 = item(order_id='4')

        oa = order(order_id='A')
        o1 = order(order_id='1', subtotal='$5.00')

        amazon.associate_items_with_orders([oa, o1], [i1, i2, i3, i4])

        self.assertFalse(oa.items_matched)
        self.assertEqual(oa.items, [])
        self.assertFalse(o1.items_matched)
        self.assertEqual(o1.items, [])

    def test_associate_items_with_orders_single_item_subtotal_match(self):
        i1 = item()
        o1 = order()

        amazon.associate_items_with_orders([o1], [i1])

        self.assertTrue(o1.items_matched)
        self.assertEqual(o1.items, [i1])

    def test_associate_items_with_orders_multi_single_matches(self):
        i1 = item(order_id='1')
        o1 = order(order_id='1')
        i2 = item(order_id='2')
        o2 = order(order_id='2')
        i3 = item(order_id='3')
        o3 = order(order_id='3')

        amazon.associate_items_with_orders([o1, o2, o3], [i3, i2, i1])

        self.assertTrue(o1.items_matched)
        self.assertEqual(o1.items, [i1])

        self.assertTrue(o2.items_matched)
        self.assertEqual(o2.items, [i2])

        self.assertTrue(o3.items_matched)
        self.assertEqual(o3.items, [i3])

    def test_associate_items_with_orders_multiple_items_subtotal_match(self):
        oid1 = 'Order1'
        i1 = item(order_id=oid1, item_subtotal='$20.21', quantity=6)
        i2 = item(order_id=oid1, item_subtotal='$2.01', quantity=1)
        i3 = item(order_id=oid1, item_subtotal='$0.41')
        items = [i1, i2, i3]

        o1 = order(order_id=oid1, subtotal='$22.63')

        amazon.associate_items_with_orders([o1], items)

        self.assertTrue(o1.items_matched)
        self.assertEqual(o1.items, items)

    def test_associate_items_with_orders_multi_orders_and_items_by_track(self):
        # All items and orders have the same order id, but they shipped in
        # different packages.
        oid = 'ABC'
        i1 = item(order_id=oid, item_subtotal='$20.21', tracking='A')
        i2 = item(order_id=oid, item_subtotal='$2.01', tracking='A')
        i3 = item(order_id=oid, item_subtotal='$0.41', tracking='B')
        a_items = [i1, i2]
        b_items = [i3]

        o1 = order(order_id=oid, subtotal='$22.22', tracking='A')
        o2 = order(order_id=oid, subtotal='$0.41', tracking='B')

        amazon.associate_items_with_orders([o1, o2], a_items + b_items)

        self.assertTrue(o1.items_matched)
        self.assertEqual(o1.items, a_items)

        self.assertTrue(o2.items_matched)
        self.assertEqual(o2.items, b_items)

    def test_associate_items_with_orders_multi_orders_and_items_by_combi(self):
        # Sometimes the same item is shipped in different packages, so tracking
        # number doesn't work.
        items = [
            item(order_id='A', item_subtotal='$2.00', tracking='A')
            for i in range(15)
        ]

        o1 = order(order_id='A', subtotal='$4.00', tracking='A')
        o2 = order(order_id='A', subtotal='$12.00', tracking='B')
        o3 = order(order_id='A', subtotal='$14.00', tracking='C')

        amazon.associate_items_with_orders([o1, o2, o3], items)

        self.assertTrue(o1.items_matched)
        self.assertEqual(len(o1.items), 2)

        self.assertTrue(o2.items_matched)
        self.assertEqual(len(o2.items), 6)

        self.assertTrue(o3.items_matched)
        self.assertEqual(len(o3.items), 7)


class OrderClass(unittest.TestCase):
    def test_constructor(self):
        o = order()

        self.assertEqual(o.order_id, '123-3211232-7655671')
        self.assertEqual(o.order_status, 'Shipped')

        # Currency fields are all in microusd.
        self.assertEqual(o.shipping_charge, 0)
        self.assertEqual(o.subtotal, 10900000)
        self.assertEqual(o.tax_charged, 1050000)
        self.assertEqual(o.tax_before_promotions, 1050000)
        self.assertEqual(o.total_charged, 11950000)
        self.assertEqual(o.total_promotions, 0)

        # Dates are parsed:
        self.assertEqual(o.order_date, date(2014, 2, 26))
        self.assertEqual(o.shipment_date, date(2014, 2, 28))

        # Tracking is renamed:
        self.assertEqual(o.tracking, 'AMZN(ABC123)')

    def test_sum_subtotals(self):
        self.assertEqual(Order.sum_subtotals([]), 0)

        o1 = order(subtotal='$5.55')
        self.assertEqual(Order.sum_subtotals([o1]), 5550000)

        o2 = order(subtotal='$1.01')
        self.assertEqual(Order.sum_subtotals([o1, o2]), 6560000)
        self.assertEqual(Order.sum_subtotals([o2, o1]), 6560000)

        o3 = order(subtotal='-$6.01')
        self.assertEqual(Order.sum_subtotals([o1, o3]), -460000)

    def test_total_by_items(self):
        o1 = order(subtotal='$5.55')
        self.assertEqual(o1.total_by_items(), 0)

        o1.set_items([
            item(item_total='$4.44'),
        ])
        self.assertEqual(o1.total_by_items(), 4440000)

        o2 = order(subtotal='$5.55')
        o2.set_items([
            item(item_total='$4.44'),
            item(item_total='$0.01'),
            item(item_total='$60.00'),
        ])
        self.assertEqual(o2.total_by_items(), 64450000)

        o3 = order(
            subtotal='$5.55',
            shipping_charge='$4.99',
            total_promotions='$4.99')
        o3.set_items([
            item(item_total='$6.44'),
        ])
        self.assertEqual(o3.total_by_items(), 6440000)

    def test_total_by_subtotals(self):
        o1 = order(
            subtotal='$5.55',
            tax_charged='$0.61',
            shipping_charge='$4.99',
            total_promotions='$4.99')
        self.assertEqual(o1.total_by_subtotals(), 6160000)

        o1.set_items([
            item(item_total='$4.44'),
        ])
        self.assertEqual(o1.total_by_subtotals(), 6160000)

        o2 = order(
            subtotal='$5.55',
            tax_charged='$0.61',
            shipping_charge='$4.99',
            total_promotions='$0.00')
        self.assertEqual(o2.total_by_subtotals(), 11150000)

    def test_transact_date(self):
        self.assertEqual(order().transact_date(), date(2014, 2, 28))

    def test_transact_amount(self):
        self.assertEqual(order().transact_amount(), 11950000)

    def test_match(self):
        o = order()

        self.assertFalse(o.matched)
        self.assertEqual(o.trans_id, None)

        o.match(transaction(id='abc'))

        self.assertTrue(o.matched)
        self.assertEqual(o.trans_id, 'abc')

    def test_set_items(self):
        o = order()
        self.assertFalse(o.items_matched)
        self.assertEqual(o.items, [])

        i1 = item()
        i2 = item()
        self.assertFalse(i1.matched)
        self.assertEqual(i1.order, None)
        self.assertFalse(i2.matched)
        self.assertEqual(i2.order, None)

        o.set_items([i1, i2])
        self.assertTrue(o.items_matched)
        self.assertEqual(o.items, [i1, i2])

        self.assertTrue(i1.matched)
        self.assertEqual(i1.order, o)
        self.assertTrue(i2.matched)
        self.assertEqual(i2.order, o)

    def test_get_note(self):
        self.assertTrue(
            'Amazon order id: 123-3211232-7655671' in order().get_note())
        self.assertTrue(
            'Buyer: Some Great Buyer (yup@aol.com)' in order().get_note())
        self.assertTrue('Order date: 2014-02-26' in order().get_note())
        self.assertTrue('Ship date: 2014-02-28' in order().get_note())
        self.assertTrue('Tracking: AMZN(ABC123)' in order().get_note())

    def test_attribute_subtotal_diff_to_misc_charge_no_diff(self):
        o = order(total_charged='$10.00', subtotal='$10.00')
        i = item(item_total='$10.00')
        o.set_items([i])

        self.assertFalse(o.attribute_subtotal_diff_to_misc_charge())

    def test_attribute_subtotal_diff_to_misc_charge(self):
        o = order(
            total_charged='$10.00', subtotal='$6.01', tax_charged='$0.00')
        i = item(item_total='$6.01')
        o.set_items([i])

        self.assertTrue(o.attribute_subtotal_diff_to_misc_charge())
        self.assertEqual(o.subtotal, 10000000)
        self.assertEqual(len(o.items), 2)
        self.assertEqual(o.items[1].item_total, 3990000)
        self.assertEqual(o.items[1].item_subtotal, 3990000)
        self.assertEqual(o.items[1].item_subtotal_tax, 0)
        self.assertEqual(o.items[1].quantity, 1)
        self.assertEqual(o.items[1].category, 'Shopping')
        self.assertEqual(o.items[1].title, 'Misc Charge (Gift wrap, etc)')

    def test_attribute_itemized_diff_to_shipping_tax_no_shipping(self):
        self.assertFalse(order().attribute_itemized_diff_to_shipping_tax())

    def test_attribute_itemized_diff_to_shipping_tax_matches(self):
        o = order(
            total_charged='$10.00',
            subtotal='$6.01',
            tax_charged='$0.00',
            shipping_charge='$3.99')
        i = item(item_total='$6.01')
        o.set_items([i])

        self.assertFalse(o.attribute_itemized_diff_to_shipping_tax())

    def test_attribute_itemized_diff_to_shipping_tax_mismatch(self):
        o = order(
            total_charged='$10.00',
            subtotal='$5.50',
            tax_charged='$1.40',
            tax_before_promotions='$1.40',
            shipping_charge='$3.99')
        i = item(item_total='$5.50')
        o.set_items([i])

        self.assertTrue(o.attribute_itemized_diff_to_shipping_tax())
        self.assertEqual(o.shipping_charge, 4500000)
        self.assertEqual(o.tax_charged, 890000)
        self.assertEqual(o.tax_before_promotions, 890000)

    def test_attribute_itemized_diff_to_per_item_tax_correct(self):
        o = order(
            total_charged='$10.00',
            subtotal='$9.00',
            tax_charged='$1.00')
        i = item(
            item_total='$10.00',
            item_subtotal='$9.00',
            item_subtotal_tax='$1.00')
        o.set_items([i])

        self.assertFalse(o.attribute_itemized_diff_to_per_item_tax())

    def test_attribute_itemized_diff_to_per_item_tax_not_just_tax(self):
        o = order(
            total_charged='$10.00',
            subtotal='$9.00',
            tax_charged='$1.00')
        # The difference in the order total and the itemized total ($.50) is
        # _not_ equal to the difference in order total tax and itemized total
        # tax ($0.25).
        i = item(
            item_total='$9.50',
            item_subtotal='$9.00',
            item_subtotal_tax='$0.75')
        o.set_items([i])

        self.assertFalse(o.attribute_itemized_diff_to_per_item_tax())

    def test_attribute_itemized_diff_to_per_item_tax_itemize_due_to_tax(self):
        o = order(
            total_charged='$10.00',
            subtotal='$9.00',
            tax_charged='$1.00')
        i = item(
            item_total='$9.99',
            item_subtotal='$9.00',
            item_subtotal_tax='$0.99')
        o.set_items([i])

        self.assertTrue(o.attribute_itemized_diff_to_per_item_tax())
        self.assertEqual(i.item_total, 10000000)
        self.assertEqual(i.item_subtotal, 9000000)
        self.assertEqual(i.item_subtotal_tax, 1000000)

    def test_to_mint_transactions_free_shipping(self):
        orig_trans = transaction(amount='$20.00')

        o = order(
            total_charged='$20.00',
            shipping_charge='$3.99',
            total_promotions='$3.99')
        i1 = item(title='Item 1', item_total='$6.00', quantity='1')
        i2 = item(title='Item 2', item_total='$14.00', quantity='3')
        o.set_items([i1, i2])

        mint_trans_ship = o.to_mint_transactions(
            orig_trans, skip_free_shipping=False)
        self.assertEqual(len(mint_trans_ship), 4)
        self.assertEqual(mint_trans_ship[0].merchant, '3x Item 2')
        self.assertEqual(mint_trans_ship[0].amount, 14000000)
        self.assertEqual(mint_trans_ship[1].merchant, 'Item 1')
        self.assertEqual(mint_trans_ship[1].amount, 6000000)
        self.assertEqual(mint_trans_ship[2].merchant, 'Shipping')
        self.assertEqual(mint_trans_ship[2].category, 'Shipping')
        self.assertEqual(mint_trans_ship[2].amount, 3990000)
        self.assertEqual(mint_trans_ship[3].merchant, 'Promotion(s)')
        self.assertEqual(mint_trans_ship[3].category, 'Shipping')
        self.assertEqual(mint_trans_ship[3].amount, -3990000)
        self.assertFalse(mint_trans_ship[3].is_debit)

        mint_trans_noship = o.to_mint_transactions(
            orig_trans, skip_free_shipping=True)
        self.assertEqual(len(mint_trans_noship), 2)
        self.assertEqual(mint_trans_noship[0].merchant, '3x Item 2')
        self.assertEqual(mint_trans_noship[0].amount, 14000000)
        self.assertEqual(mint_trans_noship[1].merchant, 'Item 1')
        self.assertEqual(mint_trans_noship[1].amount, 6000000)

    def test_to_mint_transactions_ship_promo_mismatch(self):
        orig_trans = transaction(amount='$20.00')

        o = order(
            total_charged='$20.00',
            shipping_charge='$3.99',
            total_promotions='$1.00')
        i = item(title='Item 1', item_total='$20.00', quantity='4')
        o.set_items([i])

        mint_trans_ship = o.to_mint_transactions(
            orig_trans, skip_free_shipping=True)
        self.assertEqual(len(mint_trans_ship), 3)
        self.assertEqual(mint_trans_ship[0].merchant, '4x Item 1')
        self.assertEqual(mint_trans_ship[0].amount, 20000000)
        self.assertEqual(mint_trans_ship[1].merchant, 'Shipping')
        self.assertEqual(mint_trans_ship[1].category, 'Shipping')
        self.assertEqual(mint_trans_ship[1].amount, 3990000)
        self.assertEqual(mint_trans_ship[2].merchant, 'Promotion(s)')
        self.assertEqual(mint_trans_ship[2].category, 'Shopping')
        self.assertEqual(mint_trans_ship[2].amount, -1000000)
        self.assertFalse(mint_trans_ship[2].is_debit)

    def test_merge_one_order(self):
        o1 = order()
        i1 = item()
        i2 = item()
        o1.set_items([i1, i2])

        merged = Order.merge([o1])
        self.assertTrue(merged is o1)

        self.assertEqual(len(merged.items), 1)
        self.assertEqual(merged.items[0].quantity, 4)
        self.assertEqual(merged.items[0].item_total, 23900000)

    def test_merge_multi_orders(self):
        o1 = order()
        i1 = item()
        i2 = item()
        o1.set_items([i1, i2])

        o2 = order()
        i3 = item()
        o2.set_items([i3])

        o3 = order()
        i4 = item()
        i5 = item(title='Different!')
        o3.set_items([i4, i5])

        merged = Order.merge([o1, o2, o3])
        self.assertEqual(merged.total_charged, 35850000)
        self.assertEqual(merged.tax_charged, 3150000)
        self.assertEqual(merged.shipping_charge, 0)

        self.assertEqual(len(merged.items), 2)
        self.assertEqual(merged.items[0].quantity, 8)
        self.assertEqual(merged.items[0].item_total, 47800000)
        self.assertEqual(merged.items[1].quantity, 2)
        self.assertEqual(merged.items[1].item_total, 11950000)


class ItemClass(unittest.TestCase):
    def test_constructor(self):
        i = item()

        self.assertEqual(i.order_id, '123-3211232-7655671')
        self.assertEqual(i.order_status, 'Shipped')
        self.assertEqual(i.title, 'Duracell AAs')
        self.assertEqual(i.quantity, 2)

        # Currency fields are all in microusd.
        self.assertEqual(i.purchase_price_per_unit, 5450000)
        self.assertEqual(i.item_subtotal, 10900000)
        self.assertEqual(i.item_subtotal_tax, 1050000)
        self.assertEqual(i.item_total, 11950000)

        # Dates are parsed:
        self.assertEqual(i.order_date, date(2014, 2, 26))
        self.assertEqual(i.shipment_date, date(2014, 2, 28))

        # Tracking is renamed:
        self.assertEqual(i.tracking, 'AMZN(ABC123)')

    def test_sum_subtotals(self):
        self.assertEqual(Item.sum_subtotals([]), 0)

        i1 = item(item_subtotal='$5.43')
        self.assertEqual(Item.sum_subtotals([i1]), 5430000)

        i2 = item(item_subtotal='$44.11')
        self.assertEqual(Item.sum_subtotals([i1, i2]), 49540000)
        self.assertEqual(Item.sum_subtotals([i2, i1]), 49540000)

        i3 = item(item_subtotal='-$2.11')
        self.assertEqual(Item.sum_subtotals([i2, i1, i3]), 47430000)

    def test_sum_totals(self):
        self.assertEqual(Item.sum_totals([]), 0)

        i1 = item(item_total='-$5.43')
        self.assertEqual(Item.sum_totals([i1]), -5430000)

        i2 = item(item_total='-$44.11')
        self.assertEqual(Item.sum_totals([i1, i2]), -49540000)
        self.assertEqual(Item.sum_totals([i2, i1]), -49540000)

        i3 = item(item_total='$2.11')
        self.assertEqual(Item.sum_totals([i2, i1, i3]), -47430000)

    def test_sum_subtotals_tax(self):
        self.assertEqual(Item.sum_subtotals_tax([]), 0)

        i1 = item(item_subtotal_tax='$0.43')
        self.assertEqual(Item.sum_subtotals_tax([i1]), 430000)

        i2 = item(item_subtotal_tax='$0.00')
        self.assertEqual(Item.sum_subtotals_tax([i1, i2]), 430000)
        self.assertEqual(Item.sum_subtotals_tax([i2, i1]), 430000)

        i3 = item(item_subtotal_tax='$2.11')
        self.assertEqual(Item.sum_subtotals_tax([i2, i1, i3]), 2540000)

    def test_get_title(self):
        i = item(title='The best item ever!')
        self.assertEqual(i.get_title(), '2x The best item ever')
        self.assertEqual(i.get_title(10), '2x The best')

        i2 = item(title='Something alright (]][', quantity=1)
        self.assertEqual(i2.get_title(), 'Something alright')

    def test_is_cancelled(self):
        self.assertTrue(item(order_status='Cancelled').is_cancelled())
        self.assertFalse(item(order_status='Shipped').is_cancelled())

    def test_set_quantity(self):
        i = item()
        i.set_quantity(3)

        self.assertEqual(i.quantity, 3)
        self.assertEqual(i.item_subtotal, 16350000)
        self.assertEqual(i.item_subtotal_tax, 1575000)
        self.assertEqual(i.item_total, 17925000)

        i.set_quantity(1)

        self.assertEqual(i.quantity, 1)
        self.assertEqual(i.item_subtotal, 5450000)
        self.assertEqual(i.item_subtotal_tax, 525000)
        self.assertEqual(i.item_total, 5975000)

    def test_split_by_quantity(self):
        i = item()
        items = i.split_by_quantity()

        self.assertEqual(len(items), 2)
        for it in items:
            self.assertEqual(it.quantity, 1)
            self.assertEqual(it.item_subtotal, 5450000)
            self.assertEqual(it.item_subtotal_tax, 525000)
            self.assertEqual(it.item_total, 5975000)

    def test_merge(self):
        i1 = item()
        i2 = item()
        i3 = item(title='Something diff')

        merged = Item.merge([i1, i2, i3])

        self.assertEqual(len(merged), 2)

        self.assertEqual(merged[0].quantity, 4)
        self.assertEqual(merged[0].item_subtotal, 21800000)
        self.assertEqual(merged[0].item_subtotal_tax, 2100000)
        self.assertEqual(merged[0].item_total, 23900000)

        self.assertEqual(merged[1].quantity, 2)
        self.assertEqual(merged[1].item_subtotal, 10900000)
        self.assertEqual(merged[1].item_subtotal_tax, 1050000)
        self.assertEqual(merged[1].item_total, 11950000)


class RefundClass(unittest.TestCase):
    def test_constructor(self):
        r = refund()

        self.assertEqual(r.order_id, '123-3211232-7655671')
        self.assertEqual(r.quantity, 2)

        # Currency fields are all in microusd.
        self.assertEqual(r.refund_amount, 10900000)
        self.assertEqual(r.refund_tax_amount, 1050000)

        # Dates are parsed:
        self.assertEqual(r.order_date, date(2014, 2, 26))
        self.assertEqual(r.refund_date, date(2014, 3, 16))

    def test_sum_total_refunds(self):
        self.assertEqual(Refund.sum_total_refunds([]), 0)

        r1 = refund(refund_amount='$0.43', refund_tax_amount='$0.40')
        self.assertEqual(Refund.sum_total_refunds([r1]), 830000)

        r2 = refund(refund_amount='$0.00', refund_tax_amount='$0.00')
        self.assertEqual(Refund.sum_total_refunds([r1, r2]), 830000)
        self.assertEqual(Refund.sum_total_refunds([r2, r1]), 830000)

        r3 = refund(refund_amount='$2.11', refund_tax_amount='$1.40')
        self.assertEqual(Refund.sum_total_refunds([r2, r1, r3]), 4340000)

    def test_transact_date(self):
        self.assertEqual(refund().transact_date(), date(2014, 3, 16))

    def test_transact_amount(self):
        self.assertEqual(refund().transact_amount(), -11950000)

    def test_match(self):
        r = refund()

        self.assertFalse(r.matched)
        self.assertEqual(r.trans_id, None)

        r.match(transaction(id='ABC'))

        self.assertTrue(r.matched)
        self.assertEqual(r.trans_id, 'ABC')

    def test_get_title(self):
        self.assertEqual(
            refund(title='Great item').get_title(), '2x Great item')

    def test_get_note(self):
        r = refund()

        self.assertTrue(
            'Amazon refund for order id: 123-3211232-7655671' in r.get_note())
        self.assertTrue('Buyer: Some Great Buyer' in r.get_note())
        self.assertTrue('Order date: 2014-02-26' in r.get_note())
        self.assertTrue('Refund date: 2014-03-16' in r.get_note())
        self.assertTrue('Refund reason: Customer Return' in r.get_note())

    def test_to_mint_transaction(self):
        r = refund(title='Duracell Procell AA 24 Pack')
        t = transaction(amount='$11.95', is_debit=False)

        new_trans = r.to_mint_transaction(t)

        self.assertEqual(new_trans.id, t.id)
        self.assertEqual(new_trans.amount, t.amount)
        self.assertEqual(new_trans.merchant, '2x Duracell Procell AA 24 Pack')
        self.assertFalse(new_trans.is_debit)

    def test_merge(self):
        r1 = refund()
        r2 = refund()

        merged = Refund.merge([r1, r2])

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].title, r1.title)
        self.assertEqual(merged[0].quantity, 4)
        self.assertEqual(merged[0].total_refund_amount, 47800000)
        self.assertEqual(merged[0].refund_amount, 43600000)
        self.assertEqual(merged[0].refund_tax_amount, 4200000)


if __name__ == '__main__':
    unittest.main()
