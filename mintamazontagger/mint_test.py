from datetime import date
import unittest

from mintamazontagger import category
from mintamazontagger import mint
from mintamazontagger.mint import Transaction
from mintamazontagger.mockdata import transaction, MINT_CATEGORIES


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
        self.assertEqual(
            mint.parse_mint_date('2020-01-10'),
            date(2020, 1, 10))
        self.assertEqual(
            mint.parse_mint_date('2022-11-30'),
            date(2022, 11, 30))
        self.assertEqual(
            mint.parse_mint_date('2019-10-08'),
            date(2019, 10, 8))


class TransactionClass(unittest.TestCase):
    def test_constructor(self):
        trans = transaction()
        self.assertEqual(trans.amount, -11950000)
        self.assertEqual(trans.date, date(2014, 2, 28))
        self.assertFalse(trans.matched)
        self.assertEqual(trans.orders, [])
        self.assertEqual(trans.children, [])

        trans = transaction(amount=-423.12)
        self.assertEqual(trans.amount, -423120000)

    def test_split(self):
        trans = transaction()
        strans = trans.split(1234, 'Shopping', 'Some new item', 'Test note')
        self.assertNotEqual(trans, strans)
        self.assertEqual(strans.amount, 1234)
        self.assertEqual(strans.category.name, 'Shopping')
        self.assertEqual(strans.description, 'Some new item')
        self.assertEqual(strans.notes, 'Test note')

    def test_match(self):
        trans = transaction()
        orders = [1, 2, 3]
        trans.match(orders)

        self.assertTrue(trans.matched)
        self.assertEqual(trans.orders, orders)

    def test_bastardize(self):
        child = transaction(parent_id=123)
        self.assertEqual(child.parent_id, 123)
        child.bastardize()
        self.assertEqual(child.parent_id, None)

    def test_update_category_id(self):
        trans = transaction()
        # Give it a mismatch initially:
        trans.category.id = 99
        trans.update_category_id(MINT_CATEGORIES)
        self.assertEqual(trans.category.id, '18888881_4')

        trans.category.name = 'SOME INVALID CAT'
        with self.assertRaises(AssertionError):
            trans.update_category_id(MINT_CATEGORIES)

        trans.category.name = 'Shopping'
        trans.update_category_id(MINT_CATEGORIES)
        self.assertEqual(trans.category.id, '18888881_2')

    def test_get_compare_tuple(self):
        trans = transaction(
            description='Simple Title',
            amount=-1.00)
        self.assertEqual(
            trans.get_compare_tuple(),
            ('Simple Title', '-$1.00', 'Great note here', 'Personal Care'))

        trans2 = transaction(
            description='Simple Refund',
            amount=2.01)
        self.assertEqual(
            trans2.get_compare_tuple(True),
            ('Simple Refund', '$2.01', 'Great note here'))

    def test_dry_run_str(self):
        trans = transaction()

        self.assertTrue('2014-02-28' in trans.dry_run_str())
        self.assertTrue('-$11.95' in trans.dry_run_str())
        self.assertTrue('Personal Care' in trans.dry_run_str())
        self.assertTrue('Amazon' in trans.dry_run_str())

        self.assertTrue('--IGNORED--' in trans.dry_run_str(True))
        self.assertFalse('Personal Care' in trans.dry_run_str(True))

    def test_sum_amounts(self):
        self.assertEqual(Transaction.sum_amounts([]), 0)

        trans1 = transaction(
            amount=-2.34)
        self.assertEqual(Transaction.sum_amounts([trans1]), -2340000)

        trans2 = transaction(
            amount=-8.00)
        self.assertEqual(
            Transaction.sum_amounts([trans1, trans2]),
            -10340000)

        credit = transaction(
            amount=20.20)
        self.assertEqual(
            Transaction.sum_amounts([trans1, credit, trans2]),
            9860000)

    def test_unsplit(self):
        self.assertEqual(Transaction.unsplit([]), [])

        not_child1 = transaction(
            amount=-1.00)
        self.assertEqual(Transaction.unsplit([not_child1]), [not_child1])

        not_child2 = transaction(
            amount=-2.00)
        self.assertEqual(
            Transaction.unsplit([not_child1, not_child2]),
            [not_child1, not_child2])

        child1_to_1 = transaction(
            amount=-3.00,
            parent_id=1)
        child2_to_1 = transaction(
            amount=-4.00,
            parent_id=1)
        child3_to_1 = transaction(
            amount=8.00,
            parent_id=1)
        child1_to_99 = transaction(
            amount=-5.00,
            parent_id=99)

        one_child_actual = Transaction.unsplit([child1_to_1])
        self.assertEqual(one_child_actual[0].amount, child1_to_1.amount)
        self.assertEqual(one_child_actual[0].parent_id, None)
        self.assertEqual(one_child_actual[0].id, 1)
        self.assertEqual(one_child_actual[0].children, [child1_to_1])

        three_children = [child1_to_1, child2_to_1, child3_to_1]
        three_child_actual = Transaction.unsplit(three_children)
        self.assertEqual(three_child_actual[0].amount, 1000000)
        self.assertEqual(three_child_actual[0].parent_id, None)
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
        trans1 = transaction(amount=-5.00, description='ABC')
        trans2 = transaction(
            amount=-5.00,
            description='ABC',
            category='Shipping')

        self.assertTrue(Transaction.old_and_new_are_identical(
            trans1, [trans1]))
        self.assertFalse(Transaction.old_and_new_are_identical(
            trans1, [trans2]))
        self.assertTrue(Transaction.old_and_new_are_identical(
            trans1, [trans2], True))

        new_trans = [
            transaction(amount=-2.50, description='ABC'),
            transaction(amount=-2.50, description='ABC'),
        ]
        trans1.children = new_trans
        self.assertTrue(Transaction.old_and_new_are_identical(
            trans1, new_trans))

    def test_itemize_new_trans(self):
        self.assertEqual(mint.itemize_new_trans([], 'Sweet: '), [])

        trans = [
            transaction(amount=-5.00, description='ABC'),
            transaction(amount=-15.00, description='CBA'),
        ]
        itemized_trans = mint.itemize_new_trans(trans, 'Sweet: ')
        self.assertEqual(itemized_trans[0].description, 'Sweet: CBA')
        self.assertEqual(itemized_trans[0].amount, -15000000)
        self.assertEqual(itemized_trans[1].description, 'Sweet: ABC')
        self.assertEqual(itemized_trans[1].amount, -5000000)

    def test_summarize_new_trans(self):
        original_trans = transaction(
            amount=-40.00,
            description='Amazon',
            notes='Test note')

        item1 = transaction(
            amount=-15.00,
            description='Item 1')
        item2 = transaction(
            amount=-25.00,
            description='Item 2')
        shipping = transaction(
            amount=-5.00,
            description='Shipping')
        free_shipping = transaction(
            amount=-5.00,
            description='Promotion(s)')

        actual_summary = mint.summarize_new_trans(
            original_trans,
            [item1, item2, shipping, free_shipping],
            'Amazon.com: ')[0]

        self.assertEqual(actual_summary.amount, original_trans.amount)
        self.assertEqual(
            actual_summary.category.name, category.DEFAULT_MINT_CATEGORY)
        self.assertEqual(actual_summary.description,
                         'Amazon.com: Item 1, Item 2')
        self.assertTrue('Item 1' in actual_summary.notes)
        self.assertTrue('Item 2' in actual_summary.notes)
        self.assertTrue('Shipping' in actual_summary.notes)
        self.assertTrue('Promotion(s)' in actual_summary.notes)

    def test_summarize_new_trans_one_item_keeps_category(self):
        original_trans = transaction(
            amount=-40.00,
            description='Amazon',
            notes='Test note')

        item1 = transaction(
            amount=-15.00,
            description='Giant paper shredder',
            category='Office Supplies')
        shipping = transaction(
            amount=-5.00,
            description='Shipping')
        free_shipping = transaction(
            amount=-5.00,
            description='Promotion(s)')

        actual_summary = mint.summarize_new_trans(
            original_trans,
            [item1, shipping, free_shipping],
            'Amazon.com: ')[0]

        self.assertEqual(actual_summary.amount, original_trans.amount)
        self.assertEqual(actual_summary.category.name, 'Office Supplies')
        self.assertEqual(actual_summary.description,
                         'Amazon.com: Giant paper shredder')
        self.assertTrue('Giant paper shredder' in actual_summary.notes)
        self.assertTrue('Shipping' in actual_summary.notes)
        self.assertTrue('Promotion(s)' in actual_summary.notes)


if __name__ == '__main__':
    unittest.main()
