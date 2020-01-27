from datetime import datetime, date
import unittest

from mintamazontagger import category
from mintamazontagger import mint
from mintamazontagger.mint import Transaction
from mintamazontagger.mockdata import transaction


class HelpMethods(unittest.TestCase):
    def test_truncate_title(self):
        self.assertEqual(
            mint.truncate_title('Some great title [', 20),
            'Some great title')
        self.assertEqual(
            mint.truncate_title(' Some great title abc', 5),
            'Some')
        self.assertEqual(
            mint.truncate_title('S', 1),
            'S')
        self.assertEqual(
            mint.truncate_title('Some great title [', 20, 'Amazon: '),
            'Amazon: Some great')
        self.assertEqual(
            mint.truncate_title('Some great title [', 20, '2x: '),
            '2x: Some great title')

    def test_convertCamel_to_underscores(self):
        self.assertEqual(
            mint.convertCamel_to_underscores('somethingGreatIsThis'),
            'something_great_is_this')
        self.assertEqual(
            mint.convertCamel_to_underscores('oneword'),
            'oneword')
        self.assertEqual(
            mint.convertCamel_to_underscores('CapCase?'),
            'cap_case?')

    def test_parse_mint_date(self):
        current_year = datetime.isocalendar(date.today())[0]
        self.assertEqual(
            mint.parse_mint_date('Jan 10'),
            date(current_year, 1, 10))
        self.assertEqual(
            mint.parse_mint_date('Nov 30'),
            date(current_year, 11, 30))
        self.assertEqual(
            mint.parse_mint_date('Oct 08'),
            date(current_year, 10, 8))

        self.assertEqual(
            mint.parse_mint_date('10/8/10'),
            date(2010, 10, 8))
        self.assertEqual(
            mint.parse_mint_date('1/23/10'),
            date(2010, 1, 23))
        self.assertEqual(
            mint.parse_mint_date('6/1/01'),
            date(2001, 6, 1))


class TransactionClass(unittest.TestCase):
    def test_constructor(self):
        trans = transaction()
        self.assertEqual(trans.amount, 11950000)
        self.assertTrue(trans.is_debit)
        self.assertEqual(trans.date, date(2014, 2, 28))
        self.assertFalse(trans.matched)
        self.assertEqual(trans.orders, [])
        self.assertEqual(trans.children, [])

        trans = transaction(amount='$423.12', is_debit=False)
        self.assertEqual(trans.amount, -423120000)
        self.assertFalse(trans.is_debit)

    def test_split(self):
        trans = transaction()
        strans = trans.split(1234, 'Shopping', 'Some new item', 'Test note')
        self.assertNotEqual(trans, strans)
        self.assertEqual(strans.amount, 1234)
        self.assertEqual(strans.category, 'Shopping')
        self.assertEqual(strans.merchant, 'Some new item')
        self.assertEqual(strans.note, 'Test note')

    def test_match(self):
        trans = transaction()
        orders = [1, 2, 3]
        trans.match(orders)

        self.assertTrue(trans.matched)
        self.assertEqual(trans.orders, orders)

    def test_bastardize(self):
        child = transaction(pid=123)
        self.assertTrue(child.is_child)
        self.assertEqual(child.pid, 123)

        child.bastardize()

        self.assertFalse(child.is_child)
        self.assertFalse(hasattr(child, 'pid'))

    def test_update_category_id(self):
        trans = transaction()
        # Give it a mismatch initially:
        trans.category_id = 99
        trans.update_category_id(category.DEFAULT_MINT_CATEGORIES_TO_IDS)
        self.assertEqual(trans.category_id, 4)

        trans.category = 'SOME INVALID CAT'
        with self.assertRaises(AssertionError):
            trans.update_category_id(category.DEFAULT_MINT_CATEGORIES_TO_IDS)

        trans.category = 'Shopping'
        trans.update_category_id(category.DEFAULT_MINT_CATEGORIES_TO_IDS)
        self.assertEqual(trans.category_id, 2)

    def test_get_compare_tuple(self):
        trans = transaction(
            merchant='Simple Title',
            amount='$1.00')
        self.assertEqual(
            trans.get_compare_tuple(),
            ('Simple Title', '$1.00', 'Great note here', 'Personal Care'))

        trans2 = transaction(
            merchant='Simple Refund',
            amount='$2.01',
            is_debit=False)
        self.assertEqual(
            trans2.get_compare_tuple(True),
            ('Simple Refund', '-$2.01', 'Great note here'))

    def test_dry_run_str(self):
        trans = transaction()

        self.assertTrue('2/28/14' in trans.dry_run_str())
        self.assertTrue('$11.95' in trans.dry_run_str())
        self.assertTrue('Personal Care' in trans.dry_run_str())
        self.assertTrue('Amazon' in trans.dry_run_str())

        self.assertTrue('--IGNORED--' in trans.dry_run_str(True))
        self.assertFalse('Personal Care' in trans.dry_run_str(True))

    def test_sum_amounts(self):
        self.assertEqual(Transaction.sum_amounts([]), 0)

        trans1 = transaction(
            amount='$2.34')
        self.assertEqual(Transaction.sum_amounts([trans1]), 2340000)

        trans2 = transaction(
            amount='$8.00')
        self.assertEqual(
            Transaction.sum_amounts([trans1, trans2]),
            10340000)

        credit = transaction(
            amount='$20.20',
            is_debit=False)
        self.assertEqual(
            Transaction.sum_amounts([trans1, credit, trans2]),
            -9860000)

    def test_unsplit(self):
        self.assertEqual(Transaction.unsplit([]), [])

        not_child1 = transaction(
            amount='$1.00')
        self.assertEqual(Transaction.unsplit([not_child1]), [not_child1])

        not_child2 = transaction(
            amount='$2.00')
        self.assertEqual(
            Transaction.unsplit([not_child1, not_child2]),
            [not_child1, not_child2])

        child1_to_1 = transaction(
            amount='$3.00',
            pid=1)
        child2_to_1 = transaction(
            amount='$4.00',
            pid=1)
        child3_to_1 = transaction(
            amount='$8.00',
            is_debit=False,
            pid=1)
        child1_to_99 = transaction(
            amount='$5.00',
            pid=99)

        one_child_actual = Transaction.unsplit([child1_to_1])
        self.assertEqual(one_child_actual[0].amount, child1_to_1.amount)
        self.assertFalse(one_child_actual[0].is_child)
        self.assertEqual(one_child_actual[0].id, 1)
        self.assertEqual(one_child_actual[0].children, [child1_to_1])

        three_children = [child1_to_1, child2_to_1, child3_to_1]
        three_child_actual = Transaction.unsplit(three_children)
        self.assertEqual(three_child_actual[0].amount, -1000000)
        self.assertFalse(three_child_actual[0].is_child)
        self.assertEqual(three_child_actual[0].id, 1)
        self.assertEqual(three_child_actual[0].children, three_children)

        crazy_actual = Transaction.unsplit(
            [not_child1, not_child2] + three_children + [child1_to_99])
        self.assertEqual(crazy_actual[0], not_child1)
        self.assertEqual(crazy_actual[1], not_child2)
        self.assertEqual(crazy_actual[2].id, 1)
        self.assertEqual(crazy_actual[2].children, three_children)
        self.assertEqual(crazy_actual[3].id, 99)
        self.assertEqual(crazy_actual[3].children, [child1_to_99])

    def test_old_and_new_are_identical(self):
        trans1 = transaction(amount='$5.00', merchant='ABC')
        trans2 = transaction(
            amount='$5.00',
            merchant='ABC',
            category='Shipping')

        self.assertTrue(Transaction.old_and_new_are_identical(
            trans1, [trans1]))
        self.assertFalse(Transaction.old_and_new_are_identical(
            trans1, [trans2]))
        self.assertTrue(Transaction.old_and_new_are_identical(
            trans1, [trans2], True))

        new_trans = [
            transaction(amount='$2.50', merchant='ABC'),
            transaction(amount='$2.50', merchant='ABC'),
        ]
        trans1.children = new_trans
        self.assertTrue(Transaction.old_and_new_are_identical(
            trans1, new_trans))

    def test_itemize_new_trans(self):
        self.assertEqual(mint.itemize_new_trans([], 'Sweet: '), [])

        trans = [
            transaction(amount='$5.00', merchant='ABC'),
            transaction(amount='$15.00', merchant='CBA'),
        ]
        itemized_trans = mint.itemize_new_trans(trans, 'Sweet: ')
        self.assertEqual(itemized_trans[0].merchant, 'Sweet: CBA')
        self.assertEqual(itemized_trans[0].amount, 15000000)
        self.assertEqual(itemized_trans[1].merchant, 'Sweet: ABC')
        self.assertEqual(itemized_trans[1].amount, 5000000)

    def test_summarize_new_trans(self):
        original_trans = transaction(
            amount='$40.00',
            merchant='Amazon',
            note='Test note')

        item1 = transaction(
            amount='$15.00',
            merchant='Item 1')
        item2 = transaction(
            amount='$25.00',
            merchant='Item 2')
        shipping = transaction(
            amount='$5.00',
            merchant='Shipping')
        free_shipping = transaction(
            amount='$5.00',
            merchant='Promotion(s)')

        actual_summary = mint.summarize_new_trans(
            original_trans,
            [item1, item2, shipping, free_shipping],
            'Amazon.com: ')[0]

        self.assertEqual(actual_summary.amount, original_trans.amount)
        self.assertEqual(
            actual_summary.category, category.DEFAULT_MINT_CATEGORY)
        self.assertEqual(actual_summary.merchant, 'Amazon.com: Item 1, Item 2')
        self.assertTrue('Item 1' in actual_summary.note)
        self.assertTrue('Item 2' in actual_summary.note)
        self.assertTrue('Shipping' in actual_summary.note)
        self.assertTrue('Promotion(s)' in actual_summary.note)

    def test_summarize_new_trans_one_item_keeps_category(self):
        original_trans = transaction(
            amount='$40.00',
            merchant='Amazon',
            note='Test note')

        item1 = transaction(
            amount='$15.00',
            merchant='Giant paper shredder',
            category='Office Supplies')
        shipping = transaction(
            amount='$5.00',
            merchant='Shipping')
        free_shipping = transaction(
            amount='$5.00',
            merchant='Promotion(s)')

        actual_summary = mint.summarize_new_trans(
            original_trans,
            [item1, shipping, free_shipping],
            'Amazon.com: ')[0]

        self.assertEqual(actual_summary.amount, original_trans.amount)
        self.assertEqual(actual_summary.category, 'Office Supplies')
        self.assertEqual(actual_summary.merchant,
                         'Amazon.com: Giant paper shredder')
        self.assertTrue('Giant paper shredder' in actual_summary.note)
        self.assertTrue('Shipping' in actual_summary.note)
        self.assertTrue('Promotion(s)' in actual_summary.note)


if __name__ == '__main__':
    unittest.main()
