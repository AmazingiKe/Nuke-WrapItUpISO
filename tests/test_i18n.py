import json
import unittest

from i18n import (CATALOG, LANGUAGES, Translator, message, join_messages, error_message,
                  normalize_language, validate_catalog, status_code, status_message)


class TranslationTests(unittest.TestCase):
    def test_catalog_languages_and_placeholders_match(self):
        validate_catalog(CATALOG)
        self.assertEqual(set(LANGUAGES), {'zh-CN', 'en', 'ja'})
        invalid = {'example': {'zh-CN': '{count}', 'en': '{total}', 'ja': '{count}'}}
        with self.assertRaises(ValueError): validate_catalog(invalid)

    def test_messages_keep_parameters_and_chinese_compatibility(self):
        value = message('progress.completed', done=1, total=2)
        self.assertEqual(str(value), '已完成 1/2 个工程')
        self.assertEqual(Translator('en').render(value), 'Projects completed: 1/2')
        self.assertIn('1/2', Translator('ja').render(value))
        self.assertEqual(json.loads(json.dumps({'value': value}))['value'], str(value))

    def test_composed_messages_and_user_data(self):
        user_path = 'D:/扫描依赖/{name}.nk'
        value = join_messages(' · ', [message('action.scan'), user_path])
        self.assertEqual(Translator('en').render(value), 'Scan Dependencies · ' + user_path)
        self.assertEqual(Translator('ja').render(error_message(ValueError(message('action.scan')))), '依存関係をスキャン')
        self.assertEqual(Translator('en').render(error_message(OSError('external error'))), 'external error')

    def test_status_codes_do_not_depend_on_display_language(self):
        value = message('status.ready')
        self.assertEqual(status_code(value), 'ready')
        self.assertEqual(status_code('就绪'), 'ready')
        self.assertEqual(Translator('en').render(status_message('就绪')), 'Ready')
        self.assertEqual(Translator('en').render('就绪'), '就绪')  # Raw user data stays literal.

    def test_locale_normalization(self):
        for code in ('zh_CN', 'zh-Hans-CN', 'zh_TW'):
            self.assertEqual(normalize_language(code), 'zh-CN')
        self.assertEqual(normalize_language('ja_JP'), 'ja')
        self.assertEqual(normalize_language('de_DE'), 'en')


if __name__ == '__main__': unittest.main()
