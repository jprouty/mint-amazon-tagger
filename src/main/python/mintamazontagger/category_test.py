import unittest

from mintamazontagger.category import get_mint_category_from_unspsc


class CurrencyMethods(unittest.TestCase):
    def test_(self):
        self.assertEqual(
            get_mint_category_from_unspsc(10110000), 'Pet Food & Supplies')
        self.assertEqual(
            get_mint_category_from_unspsc(10121806), 'Pet Food & Supplies')
        self.assertEqual(
            get_mint_category_from_unspsc(10111303), 'Pet Food & Supplies')
        self.assertEqual(
            get_mint_category_from_unspsc(14110000), 'Office Supplies')
        self.assertEqual(
            get_mint_category_from_unspsc(14111525), 'Office Supplies')
        self.assertEqual(
            get_mint_category_from_unspsc(14111700), 'Home Supplies')
        self.assertEqual(
            get_mint_category_from_unspsc(14111701), 'Home Supplies')
        self.assertEqual(
            get_mint_category_from_unspsc(14111703), 'Home Supplies')
        self.assertEqual(
            get_mint_category_from_unspsc(14111803), 'Office Supplies')
        self.assertEqual(
            get_mint_category_from_unspsc(25170000), 'Service & Parts')
        self.assertEqual(
            get_mint_category_from_unspsc(30181701), 'Home Improvement')
        self.assertEqual(
            get_mint_category_from_unspsc(39101600), 'Home Improvement')
        self.assertEqual(
            get_mint_category_from_unspsc(40161504), 'Service & Parts')
        self.assertEqual(
            get_mint_category_from_unspsc(42142900), 'Personal Care')
        self.assertEqual(
            get_mint_category_from_unspsc(43211617), 'Electronics & Software')
        self.assertEqual(
            get_mint_category_from_unspsc(44121705), 'Office Supplies')
        self.assertEqual(
            get_mint_category_from_unspsc(47121701), 'Home Supplies')
        self.assertEqual(
            get_mint_category_from_unspsc(56101800), 'Baby Supplies')
        self.assertEqual(
            get_mint_category_from_unspsc(60141100), 'Toys')
