from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from settings import SettingsStore, default_settings_path


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'settings' / 'wrapitupiso_settings.json'

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
        self.path.parent.mkdir()
        self.path.write_text('{bad json', encoding='utf-8')
        store = SettingsStore(self.path, system_language='en_US')
        self.assertEqual(store.data['language'], 'en')
        self.assertTrue(list(self.path.parent.glob('wrapitupiso_settings.invalid-*.json')))
        self.assertIsInstance(json.loads(self.path.read_text()), dict)

    def test_invalid_values_use_safe_defaults(self):
        self.path.parent.mkdir()
        self.path.write_text(json.dumps({'language': [], 'layout': {'window': {'width': -1, 'height': 'bad'}, 'panes': [0, -8]}, 'only_issues': 'false', 'output_directory': 7}))
        store = SettingsStore(self.path, system_language='ja_JP')
        self.assertEqual(store.data['language'], 'ja')
        self.assertGreaterEqual(store.data['layout']['window']['width'], 900)
        self.assertEqual(store.data['layout']['panes'], [340, 880])
        self.assertFalse(store.data['only_issues'])
        self.assertEqual(store.data['output_directory'], '')

    def test_unknown_schema_preserves_file(self):
        self.path.parent.mkdir()
        content = '{"schema_version": 99, "language": "ja"}'
        self.path.write_text(content, encoding='utf-8')
        store = SettingsStore(self.path, system_language='en')
        self.assertFalse(store.save())
        self.assertEqual(self.path.read_text(encoding='utf-8'), content)

    def test_unwritable_location_does_not_crash_or_claim_saved(self):
        with patch('settings.os.replace', side_effect=PermissionError('read only')):
            store = SettingsStore(self.path, system_language='en')
            self.assertFalse(store.save())
            self.assertIsNotNone(store.error)
        self.assertFalse(self.path.exists())

    def test_frozen_settings_live_next_to_executable(self):
        with patch('settings.sys.frozen', True, create=True), patch('settings.sys.executable', str(Path(self.temp.name) / 'WrapItUpISO.exe')):
            self.assertEqual(default_settings_path(), self.path)


if __name__ == '__main__': unittest.main()
