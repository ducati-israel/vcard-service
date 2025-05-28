import unittest
from vcard import normalize_phone_number, normalize_email_address


class TestMainNormalization(unittest.TestCase):  # Renamed for clarity

    def test_normalize_phone_number(self):
        self.assertEqual('+972505600011', normalize_phone_number('0505600011'))
        self.assertEqual('+972505600011', normalize_phone_number(' 0505600011 '))
        self.assertEqual('+972505600011', normalize_phone_number('\t0505600011\t'))
        # self.assertEqual('+972505600011', normalize_phone_number('0505600011')) # Duplicate
        self.assertEqual('+972505600011', normalize_phone_number('050-5600011'))
        self.assertEqual('+972505600011', normalize_phone_number('050 5600011'))

    def test_normalize_phone_number_explicit_country_code(self):
        self.assertEqual('+1505600011', normalize_phone_number('+1505600011'))
        self.assertEqual('+1505600011', normalize_phone_number('+(1)505600011'))

    def test_normalize_invalid_phone_number(self):
        self.assertEqual('', normalize_phone_number(''))

    def test_normalize_email_address(self):
        self.assertEqual('example@gmail.com', normalize_email_address('example@gmail.com'))
        self.assertEqual('example@gmail.com', normalize_email_address('Example@gmail.com'))
        self.assertEqual('example@gmail.com', normalize_email_address(' ExampLe@gmail.Com'))
        self.assertEqual('example@gmail.com', normalize_email_address('\tExampLe@gmail.Com\t'))


if __name__ == '__main__':
    unittest.main()
