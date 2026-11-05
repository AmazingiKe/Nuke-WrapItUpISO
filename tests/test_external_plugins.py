import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from core import package, scan


class ExternalPluginTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.script = self.root / 'shot.nk'
        self.name = 'OFXcom.revisionfx.rsmb_v3'
        defaults = patch('gizmo.default_paths', return_value=[self.root])
        defaults.start()
        self.addCleanup(defaults.stop)

    def test_ofx_is_external_even_if_matching_plugin_file_exists(self):
        self.script.write_text(f'{self.name} {{\n name RSMB1\n}}\n')
        for suffix in ('absent', '.dll', '.gizmo'):
            for automatic in (False, True):
                with self.subTest(suffix=suffix, automatic=automatic):
                    candidate = self.root / (self.name + suffix)
                    if suffix != 'absent': candidate.write_text('not a gizmo')
                    plan = scan(self.script, automatic_plugins=automatic)
                    self.assertEqual(plan.plugins[0].status, '外部依赖')
                    self.assertFalse(plan.plugin_blockers)
                    target = package(plan, self.root / f'out_{suffix}_{automatic}')
                    report = json.loads((target / 'report.json').read_text(encoding='utf-8'))
                    self.assertEqual(report['plugins'][0]['status'], '外部依赖')
                    self.assertIsNone(report['plugins'][0]['packed_path'])
                    self.assertFalse(report['gizmo_dependencies_complete'])
                    self.assertFalse(list((target / 'gizmos').iterdir()))
                    self.assertEqual((target / self.script.name).read_bytes(), self.script.read_bytes())
                    readme = (target / 'README.md').read_text(encoding='utf-8')
                    self.assertIn('未成功 0 项', readme)
                    self.assertIn('接收方安装', readme)
                    self.assertFalse((target / 'launch.ps1').exists())
                    if suffix != 'absent': candidate.unlink()

    def test_ofx_child_does_not_block_parent_gizmo(self):
        self.script.write_text('Outer {\n}\n')
        (self.root / 'Outer.gizmo').write_text(f'Gizmo {{\n}}\n{self.name} {{\n}}\nend_group\n')
        plan = scan(self.script)
        self.assertFalse(plan.plugin_blockers)
        target = package(plan, self.root / 'nested')
        self.assertTrue((target / 'gizmos/Outer.gizmo').is_file())
        self.assertFalse((target / ('gizmos/' + self.name + '.gizmo')).exists())
        self.assertFalse((target / 'launch.ps1').exists())

    def test_regular_gizmo_has_manual_instructions_without_launcher(self):
        self.script.write_text('Outer {\n}\n')
        (self.root / 'Outer.gizmo').write_text('Gizmo {\n}\nend_group\n')
        target = package(scan(self.script), self.root / 'regular')
        self.assertFalse((target / 'launch.ps1').exists())
        self.assertIn('pluginAddPath', (target / 'README.md').read_text(encoding='utf-8'))
