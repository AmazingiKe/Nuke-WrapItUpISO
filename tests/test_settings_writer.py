import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from wrapitupiso.config.settings import SettingsStore, SettingsWriter, write_settings


class SettingsWriterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'wrapitupiso_settings.json'
        self.store = SettingsStore(self.path)
        self.writer = SettingsWriter(self.path, self.store.system_language)
        self.addCleanup(self.writer.close)

    def saved(self):
        return json.loads(self.path.read_text(encoding='utf-8'))

    def test_busy_writer_keeps_only_latest_independent_snapshot(self):
        entered, release = threading.Event(), threading.Event()
        writes = []
        def blocked_write(path, values):
            writes.append(values['language'])
            entered.set()
            release.wait(3)
            return write_settings(path, values)
        with patch('wrapitupiso.config.settings.write_settings', side_effect=blocked_write):
            try:
                self.store.data['language'] = 'en'
                self.writer.submit(self.store.data)
                self.assertTrue(entered.wait(2))
                self.store.data['language'] = 'zh-CN'
                self.writer.submit(self.store.data)
                self.store.data['language'] = 'ja'
                self.writer.submit(self.store.data)
                self.store.data['language'] = 'en'
            finally:
                release.set()
                self.writer.close()
        self.assertEqual(writes, ['en', 'ja'])
        self.assertEqual(self.saved()['language'], 'ja')

    def test_pause_defers_writes_until_resume(self):
        entered = threading.Event()
        def record_write(path, values):
            entered.set()
            return write_settings(path, values)
        with patch('wrapitupiso.config.settings.write_settings', side_effect=record_write):
            self.writer.pause()
            self.store.data['language'] = 'ja'
            self.writer.submit(self.store.data)
            self.writer.wait_idle()
            self.assertFalse(entered.wait(0.1))
            self.assertEqual(self.saved()['language'], 'en')
            self.writer.resume()
            self.assertTrue(entered.wait(2))
            self.writer.close()
        self.assertEqual(self.saved()['language'], 'ja')

    def test_close_flushes_latest_even_when_paused_and_is_idempotent(self):
        self.writer.pause()
        self.store.data['language'] = 'ja'
        self.writer.submit(self.store.data)
        self.writer.close()
        self.writer.close()
        self.assertEqual(self.saved()['language'], 'ja')
        self.assertFalse(self.writer.thread.is_alive())
        self.assertEqual(list(self.path.parent.glob('.wrapitupiso-*.tmp')), [])

    def test_failed_atomic_replace_preserves_file_and_writer_can_retry(self):
        original = self.path.read_bytes()
        with patch('wrapitupiso.config.settings.os.replace', side_effect=PermissionError('locked')):
            self.store.data['language'] = 'ja'
            self.writer.submit(self.store.data)
            error = self.writer.results.get(timeout=2)
            self.assertIsInstance(error, PermissionError)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.glob('.wrapitupiso-*.tmp')), [])
        self.writer.submit(self.store.data)
        self.assertIsNone(self.writer.results.get(timeout=2))
        self.assertEqual(self.saved()['language'], 'ja')
