"""Entry points and resource paths must survive a different working directory."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class LayoutTests(unittest.TestCase):
    def test_source_resources_and_settings_are_independent_of_cwd(self):
        from wrapitupiso.config.paths import application_root, bundle_root, resource_path
        from wrapitupiso.config.settings import default_settings_path
        with tempfile.TemporaryDirectory() as folder, patch('os.getcwd', return_value=folder):
            self.assertEqual(application_root(), ROOT)
            self.assertEqual(bundle_root(), ROOT)
            self.assertEqual(default_settings_path(), ROOT / 'wrapitupiso_settings.json')
            catalog = json.loads(resource_path('locales/translations.json').read_text(encoding='utf-8'))
            self.assertTrue(catalog)
            self.assertTrue(resource_path('icons/app.ico').is_file())
            self.assertTrue(resource_path('icons/wordmark_2x.png').is_file())

    def test_frozen_resources_and_settings_use_separate_roots(self):
        from wrapitupiso.config.paths import application_root, bundle_root, resource_path
        from wrapitupiso.config.settings import default_settings_path
        with tempfile.TemporaryDirectory() as folder:
            release = Path(folder) / 'release'
            unpacked = Path(folder) / '_MEI123'
            with patch.object(sys, 'frozen', True, create=True), \
                 patch.object(sys, '_MEIPASS', str(unpacked), create=True), \
                 patch.object(sys, 'executable', str(release / 'WrapItUpISO.exe')):
                self.assertEqual(application_root(), release)
                self.assertEqual(bundle_root(), unpacked)
                self.assertEqual(resource_path('icons/app.ico'), unpacked / 'wrapitupiso/resources/icons/app.ico')
                self.assertEqual(default_settings_path(), release / 'wrapitupiso_settings.json')

    def test_cli_runs_without_site_packages_from_another_directory(self):
        expected = json.loads((ROOT / 'version.json').read_text(encoding='utf-8'))
        with tempfile.TemporaryDirectory() as folder:
            result = subprocess.run([sys.executable, '-S', str(ROOT / 'cli.py'), '--version'],
                                    cwd=folder, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), f"{expected['name']} {expected['version']}")

    def test_verification_tools_find_package_from_another_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            for tool in ('verify_gizmo_nuke.py', 'verify_scan_nuke.py',
                         'verify_delivery_nuke.py', 'benchmark_pipeline.py'):
                with self.subTest(tool=tool):
                    result = subprocess.run([sys.executable, '-S', str(ROOT / 'tools/verify' / tool), '--help'],
                                            cwd=folder, capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
