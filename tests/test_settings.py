from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from wrapitupiso.config.settings import SettingsStore, default_settings_path


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'wrapitupiso_settings.json'

    def test_default_launch_creates_only_json_beside_application(self):
        with patch('wrapitupiso.config.settings.application_root', return_value=self.path.parent):
            store = SettingsStore(system_language='ja_JP')
        self.assertEqual(store.path, self.path)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])
        self.assertEqual(store.data['language'], 'ja')

    def legacy_file(self, content):
        legacy = self.path.parent / 'settings' / self.path.name
        legacy.parent.mkdir()
        legacy.write_text(content, encoding='utf-8')
        return legacy

    def test_legacy_settings_are_imported_without_losing_original(self):
        content = json.dumps({'schema_version': 1, 'language': 'ja',
                              'layout': {'panes': [430, 790]}, 'output_directory': 'D:/delivery'})
        legacy = self.legacy_file(content)
        with patch('wrapitupiso.config.settings.application_root', return_value=self.path.parent):
            store = SettingsStore(system_language='en')
        saved = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(saved['language'], 'ja')
        self.assertEqual(saved['layout']['panes'], [430, 790])
        self.assertEqual(saved['output_directory'], 'D:/delivery')
        self.assertEqual(legacy.read_text(encoding='utf-8'), content)
        self.assertIsNone(store.error)

    def test_current_file_takes_priority_over_legacy(self):
        legacy = self.legacy_file('{"language": "ja"}')
        self.path.write_text('{"language": "en"}', encoding='utf-8')
        with patch('wrapitupiso.config.settings.application_root', return_value=self.path.parent):
            store = SettingsStore(system_language='zh_CN')
        self.assertEqual(store.data['language'], 'en')
        self.assertEqual(legacy.read_text(encoding='utf-8'), '{"language": "ja"}')

    def test_unsupported_legacy_schema_is_not_replaced(self):
        content = '{"schema_version": 99, "language": "ja"}'
        legacy = self.legacy_file(content)
        with patch('wrapitupiso.config.settings.application_root', return_value=self.path.parent):
            store = SettingsStore()
        self.assertTrue(store.read_only)
        self.assertFalse(self.path.exists())
        self.assertEqual(legacy.read_text(encoding='utf-8'), content)

    def test_legacy_preferences_remain_usable_when_new_location_is_unwritable(self):
        legacy = self.legacy_file('{"language": "ja"}')
        with patch('wrapitupiso.config.settings.application_root', return_value=self.path.parent), \
             patch('wrapitupiso.config.settings.os.replace', side_effect=PermissionError('read only')):
            store = SettingsStore(system_language='en')
        self.assertEqual(store.data['language'], 'ja')
        self.assertIsNotNone(store.error)
        self.assertFalse(self.path.exists())
        self.assertTrue(legacy.exists())

    def test_corrupt_legacy_settings_are_backed_up_before_recovery(self):
        legacy = self.legacy_file('{bad json')
        with patch('wrapitupiso.config.settings.application_root', return_value=self.path.parent):
            store = SettingsStore(system_language='en')
        self.assertTrue(store.recovered)
        backups = list(legacy.parent.glob('wrapitupiso_settings.invalid-*.json'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding='utf-8'), '{bad json')
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8'))['language'], 'en')

    def test_explicit_settings_path_does_not_import_default_legacy_file(self):
        self.legacy_file('{"language": "ja"}')
        with patch('wrapitupiso.config.settings.application_root', return_value=self.path.parent):
            store = SettingsStore(self.path, system_language='en')
        self.assertEqual(store.data['language'], 'en')

    def test_first_launch_creates_named_json_and_reload(self):
        store = SettingsStore(self.path, system_language='ja_JP')
        self.assertTrue(self.path.exists())
        self.assertEqual(store.data['language'], 'ja')
        store.data['language'] = 'en'
        store.data['layout']['window'] = {'width': 1150, 'height': 740, 'maximized': False}
        self.assertTrue(store.save())
        restored = SettingsStore(self.path, system_language='zh_CN')
        self.assertEqual(restored.data['language'], 'en')
        self.assertEqual(restored.data['layout']['window']['width'], 1150)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_invalid_settings_recover_with_backup(self):
        self.path.parent.mkdir(exist_ok=True)
        self.path.write_text('{bad json', encoding='utf-8')
        store = SettingsStore(self.path, system_language='en_US')
        self.assertEqual(store.data['language'], 'en')
        self.assertTrue(list(self.path.parent.glob('wrapitupiso_settings.invalid-*.json')))
        self.assertIsInstance(json.loads(self.path.read_text()), dict)

    def test_invalid_values_use_safe_defaults(self):
        self.path.parent.mkdir(exist_ok=True)
        self.path.write_text(json.dumps({'language': [], 'layout': {'window': {'width': -1, 'height': 'bad'}, 'panes': [0, -8]}, 'only_issues': 'false', 'output_directory': 7}))
        store = SettingsStore(self.path, system_language='ja_JP')
        self.assertEqual(store.data['language'], 'ja')
        self.assertGreaterEqual(store.data['layout']['window']['width'], 900)
        self.assertEqual(store.data['layout']['panes'], [340, 880])
        self.assertFalse(store.data['only_issues'])
        self.assertEqual(store.data['output_directory'], '')

    def test_unknown_schema_preserves_file(self):
        self.path.parent.mkdir(exist_ok=True)
        content = '{"schema_version": 99, "language": "ja"}'
        self.path.write_text(content, encoding='utf-8')
        store = SettingsStore(self.path, system_language='en')
        self.assertFalse(store.save())
        self.assertEqual(self.path.read_text(encoding='utf-8'), content)

    def test_unwritable_location_does_not_crash_or_claim_saved(self):
        with patch('wrapitupiso.config.settings.os.replace', side_effect=PermissionError('read only')):
            store = SettingsStore(self.path, system_language='en')
            self.assertFalse(store.save())
            self.assertIsNotNone(store.error)
        self.assertFalse(self.path.exists())

    def test_frozen_settings_live_next_to_executable(self):
        with patch('wrapitupiso.config.paths.sys.frozen', True, create=True), patch('wrapitupiso.config.paths.sys.executable', str(Path(self.temp.name) / 'WrapItUpISO.exe')):
            self.assertEqual(default_settings_path(), self.path)


if __name__ == '__main__': unittest.main()
