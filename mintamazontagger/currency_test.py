import unittest

from mintamazontagger import currency


class CurrencyMethods(unittest.TestCase):
    def test_micro_usd_nearly_equal(self):
        self.assertTrue(currency.micro_usd_nearly_equal(0, 10))
        self.assertTrue(currency.micro_usd_nearly_equal(0, -10))
        self.assertTrue(currency.micro_usd_nearly_equal(-10, 0))
        self.assertTrue(currency.micro_usd_nearly_equal(10, 0))

        self.assertTrue(currency.micro_usd_nearly_equal(42143241, 42143239))
        self.assertTrue(currency.micro_usd_nearly_equal(42143241, 42143243))

        self.assertFalse(currency.micro_usd_nearly_equal(0, 400))
        self.assertFalse(currency.micro_usd_nearly_equal(0, -200))
        self.assertFalse(currency.micro_usd_nearly_equal(-500, 0))
        self.assertFalse(currency.micro_usd_nearly_equal(200, 0))

    def test_round_usd(self):
        self.assertEqual(currency.round_usd(30.0003), 30.0)
        self.assertEqual(currency.round_usd(0.103), 0.10)
        self.assertEqual(currency.round_usd(303.01), 303.01)
        self.assertEqual(currency.round_usd(-103.01), -103.01)

    def test_round_micro_usd_to_cent(self):
        self.assertEqual(currency.round_micro_usd_to_cent(50505050), 50510000)
        self.assertEqual(currency.round_micro_usd_to_cent(50514550), 50510000)
        self.assertEqual(currency.round_micro_usd_to_cent(-550), 0)
        self.assertEqual(currency.round_micro_usd_to_cent(550), 0)

    def test_micro_usd_to_usd_float(self):
        self.assertEqual(currency.micro_usd_to_usd_float(5050500), 5.05)
        self.assertEqual(currency.micro_usd_to_usd_float(150500), 0.15)
        self.assertEqual(currency.micro_usd_to_usd_float(-500), 0)
        self.assertEqual(currency.micro_usd_to_usd_float(500), 0)

    def test_micro_usd_to_usd_string(self):
        self.assertEqual(currency.micro_usd_to_usd_string(1230040), '$1.23')
        self.assertEqual(currency.micro_usd_to_usd_string(-123000), '-$0.12')
        self.assertEqual(currency.micro_usd_to_usd_string(-1900), '$0.00')
        self.assertEqual(currency.micro_usd_to_usd_string(-10000), '-$0.01')

    def test_parse_usd_as_micro_usd(self):
        self.assertEqual(currency.parse_usd_as_micro_usd('$1.23'), 1230000)
        self.assertEqual(currency.parse_usd_as_micro_usd('$0.00'), 0)
        self.assertEqual(currency.parse_usd_as_micro_usd('-$0.00'), 0)
        self.assertEqual(currency.parse_usd_as_micro_usd('$55'), 55000000)
        self.assertEqual(currency.parse_usd_as_micro_usd('$12.23'), 12230000)
        self.assertEqual(currency.parse_usd_as_micro_usd('-$12.23'), -12230000)

    def test_parse_usd_as_float(self):
        self.assertEqual(currency.parse_usd_as_float('$1.23'), 1.23)
        self.assertEqual(currency.parse_usd_as_float('$0.00'), 0)
        self.assertEqual(currency.parse_usd_as_float('-$0.00'), 0)
        self.assertEqual(currency.parse_usd_as_float('$55'), 55.0)
        self.assertEqual(currency.parse_usd_as_float('$12.23'), 12.23)
        self.assertEqual(currency.parse_usd_as_float('-$12.23'), -12.23)


if __name__ == '__main__':
    unittest.main()
