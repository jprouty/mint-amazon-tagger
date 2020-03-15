from collections import Counter
import unittest

from mintamazontagger import tagger
from mintamazontagger.mockdata import item, order, refund, transaction


class Args:
    def __init__(self, **kwds):
        self.__dict__.update(kwds)


def get_args(
        description_prefix_override='Amazon.com: ',
        description_return_prefix_override='Amazon.com: ',
        amazon_domains='amazon.com,amazon.co.uk',
        mint_input_merchant_filter='amazon',
        mint_input_include_mmerchant=False,
        mint_input_include_merchant=False,
        mint_input_categories_filter=None,
        verbose_itemize=False,
        no_itemize=False,
        no_tag_categories=False,
        prompt_retag=False,
        num_updates=0,
        retag_changed=False,
        do_not_predict_categories=True,
        max_days_between_payment_and_shipping=3):
    return Args(
        description_prefix_override=description_prefix_override,
        description_return_prefix_override=description_return_prefix_override,
        amazon_domains=amazon_domains,
        mint_input_merchant_filter=mint_input_merchant_filter,
        mint_input_include_mmerchant=mint_input_include_mmerchant,
        mint_input_include_merchant=mint_input_include_merchant,
        mint_input_categories_filter=mint_input_categories_filter,
        verbose_itemize=verbose_itemize,
        no_itemize=no_itemize,
        no_tag_categories=no_tag_categories,
        prompt_retag=prompt_retag,
        num_updates=num_updates,
        retag_changed=retag_changed,
        do_not_predict_categories=do_not_predict_categories,
        max_days_between_payment_and_shipping=(
            max_days_between_payment_and_shipping),
    )


class Tagger(unittest.TestCase):
    def test_get_mint_updates_empty_input(self):
        updates, _ = tagger.get_mint_updates(
            [], [], [],
            [],
            get_args(), Counter())
        self.assertEqual(len(updates), 0)

    def test_get_mint_updates_simple_match(self):
        i1 = item()
        o1 = order()
        t1 = transaction()

        stats = Counter()
        updates, _ = tagger.get_mint_updates(
            [o1], [i1], [],
            [t1],
            get_args(), stats)

        self.assertEqual(len(updates), 1)
        orig_t, new_trans = updates[0]
        self.assertTrue(orig_t is t1)
        self.assertEqual(len(new_trans), 1)
        self.assertEqual(new_trans[0].merchant, 'Amazon.com: 2x Duracell AAs')
        self.assertEqual(new_trans[0].category, 'Electronics & Software')
        self.assertEqual(new_trans[0].amount, 11950000)
        self.assertTrue(new_trans[0].is_debit)
        self.assertFalse(new_trans[0].is_child)

        self.assertEqual(stats['new_tag'], 1)

    def test_get_mint_updates_simple_match_refund(self):
        r1 = refund(
            title='Cool item',
            refund_amount='$10.95',
            refund_tax_amount='$1.00',
            refund_date='3/12/14')
        t1 = transaction(amount='$11.95', is_debit=False, date='3/12/14')

        stats = Counter()
        updates, _ = tagger.get_mint_updates(
            [], [], [r1],
            [t1],
            get_args(), stats)

        self.assertEqual(len(updates), 1)
        orig_t, new_trans = updates[0]
        self.assertTrue(orig_t is t1)
        self.assertEqual(len(new_trans), 1)
        self.assertEqual(new_trans[0].merchant, 'Amazon.com: 2x Cool item')
        self.assertEqual(new_trans[0].category, 'Returned Purchase')
        self.assertEqual(new_trans[0].amount, -11950000)
        self.assertFalse(new_trans[0].is_debit)
        self.assertFalse(new_trans[0].is_child)

        self.assertEqual(stats['new_tag'], 1)

    def test_get_mint_updates_refund_no_date(self):
        r1 = refund(
            title='Cool item2',
            refund_amount='$10.95',
            refund_tax_amount='$1.00',
            refund_date=None)
        t1 = transaction(amount='$11.95', is_debit=False, date='3/12/14')

        stats = Counter()
        updates, _ = tagger.get_mint_updates(
            [], [], [r1],
            [t1],
            get_args(), stats)

        self.assertEqual(len(updates), 0)
        self.assertEqual(stats['new_tag'], 0)

    def test_get_mint_updates_skip_already_tagged(self):
        i1 = item()
        o1 = order()
        t1 = transaction(merchant='SomeRandoCustomPrefix: already tagged')

        stats = Counter()
        updates, _ = tagger.get_mint_updates(
            [o1], [i1], [],
            [t1],
            get_args(description_prefix_override='SomeRandoCustomPrefix: '),
            stats)

        self.assertEqual(len(updates), 0)
        self.assertEqual(stats['no_retag'], 1)

    def test_get_mint_updates_retag_arg(self):
        i1 = item()
        o1 = order()
        t1 = transaction(merchant='Amazon.com: already tagged')

        stats = Counter()
        updates, _ = tagger.get_mint_updates(
            [o1], [i1], [],
            [t1],
            get_args(retag_changed=True), stats)

        self.assertEqual(len(updates), 1)
        self.assertEqual(stats['retag'], 1)

    def test_get_mint_updates_multi_domains_no_retag(self):
        i1 = item()
        o1 = order()
        t1 = transaction(merchant='Amazon.co.uk: already tagged')

        stats = Counter()
        updates, _ = tagger.get_mint_updates(
            [o1], [i1], [],
            [t1],
            get_args(), stats)

        self.assertEqual(len(updates), 0)

    def test_get_mint_updates_no_update_for_identical(self):
        i1 = item()
        o1 = order()
        t1 = transaction(
            merchant='Amazon.com: 2x Duracell AAs',
            category='Electronics & Software',
            note=o1.get_note() + '\nItem(s):\n - 2x Duracell AAs')

        stats = Counter()
        updates, _ = tagger.get_mint_updates(
            [o1], [i1], [],
            [t1],
            get_args(retag_changed=True), stats)

        self.assertEqual(len(updates), 0)
        self.assertEqual(stats['already_up_to_date'], 1)

    def test_get_mint_updates_no_tag_categories_arg(self):
        i1 = item()
        o1 = order()
        t1 = transaction(
            merchant='Amazon.com: 2x Duracell AAs',
            note=o1.get_note() + '\nItem(s):\n - 2x Duracell AAs')

        stats = Counter()
        updates, _ = tagger.get_mint_updates(
            [o1], [i1], [],
            [t1],
            get_args(no_tag_categories=True), stats)

        self.assertEqual(len(updates), 0)
        self.assertEqual(stats['already_up_to_date'], 1)

    def test_get_mint_updates_verbose_itemize_arg(self):
        i1 = item()
        o1 = order(shipping_charge='$3.99', total_promotions='$3.99')
        t1 = transaction()

        stats = Counter()
        updates, _ = tagger.get_mint_updates(
            [o1], [i1], [],
            [t1],
            get_args(verbose_itemize=True), stats)

        self.assertEqual(len(updates), 1)
        orig_t, new_trans = updates[0]
        self.assertTrue(orig_t is t1)
        self.assertEqual(len(new_trans), 3)
        self.assertEqual(new_trans[0].merchant, 'Amazon.com: Promotion(s)')
        self.assertEqual(new_trans[0].category, 'Shipping')
        self.assertEqual(new_trans[0].amount, -3990000)
        self.assertFalse(new_trans[0].is_debit)
        self.assertEqual(new_trans[1].merchant, 'Amazon.com: Shipping')
        self.assertEqual(new_trans[1].category, 'Shipping')
        self.assertEqual(new_trans[1].amount, 3990000)
        self.assertEqual(new_trans[2].merchant, 'Amazon.com: 2x Duracell AAs')
        self.assertEqual(new_trans[2].category, 'Electronics & Software')
        self.assertEqual(new_trans[2].amount, 11950000)

        self.assertEqual(stats['new_tag'], 1)

    def test_get_mint_updates_no_itemize_arg_single_item(self):
        i1 = item()
        o1 = order(total_charged='$15.94', shipping_charge='$3.99')
        t1 = transaction(amount='$15.94')

        stats = Counter()
        updates, _ = tagger.get_mint_updates(
            [o1], [i1], [],
            [t1],
            get_args(no_itemize=True), stats)

        self.assertEqual(len(updates), 1)
        orig_t, new_trans = updates[0]
        self.assertTrue(orig_t is t1)
        self.assertEqual(len(new_trans), 1)
        self.assertEqual(new_trans[0].merchant, 'Amazon.com: 2x Duracell AAs')
        self.assertEqual(new_trans[0].category, 'Electronics & Software')
        self.assertEqual(new_trans[0].amount, 15940000)

    def test_get_mint_updates_no_itemize_arg_three_items(self):
        i1 = item(
            title='Really cool watch',
            quantity=1,
            item_subtotal='$10.00',
            item_subtotal_tax='$1.00',
            item_total='$11.00')
        i2 = item(
            title='Organic water',
            quantity=1,
            item_subtotal='$6.00',
            item_subtotal_tax='$0.00',
            item_total='$6.00')
        o1 = order(
            subtotal='$16.00',
            tax_charged='$1.00',
            total_charged='$17.00')
        t1 = transaction(amount='$17.00')

        stats = Counter()
        updates, _ = tagger.get_mint_updates(
            [o1], [i1, i2], [],
            [t1],
            get_args(no_itemize=True), stats)

        self.assertEqual(len(updates), 1)
        orig_t, new_trans = updates[0]
        self.assertTrue(orig_t is t1)
        self.assertEqual(len(new_trans), 1)
        self.assertEqual(new_trans[0].merchant,
                         'Amazon.com: Really cool watch, Organic water')
        self.assertEqual(new_trans[0].category, 'Shopping')
        self.assertEqual(new_trans[0].amount, 17000000)

    def test_get_mint_updates_multi_orders_trans_same_date_and_amount(self):
        i1 = item(order_id='A')
        o1 = order(order_id='A')
        i2 = item(order_id='B')
        o2 = order(order_id='B')
        t1 = transaction()
        t2 = transaction()

        stats = Counter()
        updates, _ = tagger.get_mint_updates(
            [o1, o2], [i1, i2], [],
            [t1, t2],
            get_args(), stats)

        self.assertEqual(len(updates), 2)

        updates2, _ = tagger.get_mint_updates(
            [o1, o2], [i1, i2], [],
            [t1, t2],
            get_args(num_updates=1), stats)

        self.assertEqual(len(updates2), 1)


if __name__ == '__main__':
    unittest.main()
