import unittest
from main import normalize_phone_number, normalize_email_address, parse_motorcycles, format_motorcycle_models, format_motorcycle_summary, build_google_wallet_motorcycle_modules, build_apple_wallet_motorcycle_front_fields

class TestMain(unittest.TestCase):

    def test_normalize_phone_number(self):
        self.assertEqual('+972505600011', normalize_phone_number('0505600011'))
        self.assertEqual('+972505600011', normalize_phone_number(' 0505600011 '))
        self.assertEqual('+972505600011', normalize_phone_number('\t0505600011\t'))
        self.assertEqual('+972505600011', normalize_phone_number('0505600011'))
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

    def test_parse_motorcycles(self):
        item = {
            'אופנוע 1 - דגם': ' StreetFighter V2 (955cc) ', 'אופנוע 1 - מספר רישוי': '02385470001', 'אופנוע 1 - שנתון': '2023',
            'אופנוע 2 - דגם': 'Panigale V2S (890cc)', 'אופנוע 2 - מספר רישוי': '02685466661', 'אופנוע 2 - שנתון': '2026',
            'אופנוע 3 - דגם': '', 'אופנוע 3 - מספר רישוי': '', 'אופנוע 3 - שנתון': '',
        }
        self.assertEqual([
            {'model': 'StreetFighter V2 (955cc)', 'licenseNumber': '02385470001', 'manufactureYear': 2023},
            {'model': 'Panigale V2S (890cc)', 'licenseNumber': '02685466661', 'manufactureYear': 2026},
        ], parse_motorcycles(item))

    def test_parse_motorcycles_three(self):
        item = {}
        for i in range(1, 4):
            item.update({f'אופנוע {i} - דגם': f'Model {i}', f'אופנוע {i} - מספר רישוי': f'{i}', f'אופנוע {i} - שנתון': '2020'})
        self.assertEqual(3, len(parse_motorcycles(item)))

    def test_parse_motorcycles_skips_empty_middle_slot(self):
        item = {
            'אופנוע 1 - דגם': '', 'אופנוע 1 - מספר רישוי': '', 'אופנוע 1 - שנתון': '',
            'אופנוע 2 - דגם': 'Monster', 'אופנוע 2 - מספר רישוי': '123', 'אופנוע 2 - שנתון': 'n/a',
        }
        self.assertEqual([{'model': 'Monster', 'licenseNumber': '123', 'manufactureYear': None}], parse_motorcycles(item))

    def test_parse_motorcycles_legacy_column(self):
        item = {'דגם אופנוע נוכחי': ' Monster 821 '}
        self.assertEqual([{'model': 'Monster 821', 'licenseNumber': '', 'manufactureYear': None}], parse_motorcycles(item))

        item = {'דגם אופנוע נוכחי': 'Monster 821', 'אופנוע 1 - דגם': '', 'אופנוע 1 - מספר רישוי': '', 'אופנוע 1 - שנתון': ''}
        self.assertEqual('Monster 821', parse_motorcycles(item)[0]['model'])

        self.assertEqual([], parse_motorcycles({'דגם אופנוע נוכחי': ''}))

    def test_format_motorcycle_models(self):
        self.assertEqual('A, B', format_motorcycle_models([{'model': 'A'}, {'model': ''}, {'model': 'B'}]))
        self.assertEqual('', format_motorcycle_models([]))

    def test_format_motorcycle_summary(self):
        self.assertEqual('', format_motorcycle_summary([]))
        self.assertEqual('A', format_motorcycle_summary([{'model': 'A'}]))
        self.assertEqual('A +1', format_motorcycle_summary([{'model': 'A'}, {'model': 'B'}]))
        self.assertEqual('B +2', format_motorcycle_summary([{'model': ''}, {'model': 'B'}, {'model': 'C'}]))

    def test_build_google_wallet_motorcycle_modules(self):
        self.assertEqual([], build_google_wallet_motorcycle_modules([]))
        self.assertEqual([], build_google_wallet_motorcycle_modules([{'model': 'A', 'licenseNumber': '', 'manufactureYear': None}]))

        modules = build_google_wallet_motorcycle_modules([{'model': 'A', 'licenseNumber': '123', 'manufactureYear': None}])
        self.assertEqual(['bike_1'], [module['id'] for module in modules])
        self.assertEqual('אופנוע', modules[0]['header'])
        self.assertEqual('A\nמספר רישוי: 123', modules[0]['body'])

        modules = build_google_wallet_motorcycle_modules([
            {'model': 'A', 'licenseNumber': '123', 'manufactureYear': 2023},
            {'model': 'B', 'licenseNumber': '', 'manufactureYear': None},
        ])
        self.assertEqual(['bike_1', 'bike_2'], [module['id'] for module in modules])
        self.assertEqual('אופנוע 2', modules[1]['header'])
        self.assertEqual('B', modules[1]['body'])
        self.assertEqual('A\nLicense number: 123\nYear of manufacture: 2023', modules[0]['localizedBody']['translatedValues'][0]['value'])

    def test_build_apple_wallet_motorcycle_front_fields(self):
        self.assertEqual([], build_apple_wallet_motorcycle_front_fields([]))

        first = {'model': 'A', 'licenseNumber': '123', 'manufactureYear': 2023}
        second = {'model': 'B', 'licenseNumber': '', 'manufactureYear': None}
        third = {'model': 'C', 'licenseNumber': '456', 'manufactureYear': None}

        fields = build_apple_wallet_motorcycle_front_fields([first])
        self.assertEqual([('bike', 'A', 1), ('model_year', '2023', 1), ('license_number', '123', 1)], [(field['label'], field['value'], field['row']) for field in fields])

        fields = build_apple_wallet_motorcycle_front_fields([first, second])
        self.assertEqual([('2023 · 123', 'A', 1), ('bike_2', 'B', 1)], [(field['label'], field['value'], field['row']) for field in fields])

        fields = build_apple_wallet_motorcycle_front_fields([first, second, third])
        self.assertEqual([('2023 · 123', 'A', 1), ('bike_2', 'B', 1), ('456', 'C', 1)], [(field['label'], field['value'], field['row']) for field in fields])
        self.assertEqual(len(fields), len(set(field['key'] for field in fields)))
