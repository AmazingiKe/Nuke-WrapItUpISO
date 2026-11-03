import hashlib
import json
import os
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest
from unittest.mock import patch

from core import scan, package


class GizmoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.plugins = self.root / 'plugins'
        self.plugins.mkdir()
        self.script = self.root / 'shot.nk'
        self.script.write_text('Root {\n}\nOuter {\n name Outer1\n}\n', encoding='utf-8')

    def gizmo(self, name, children='', folder=None, knobs=''):
        path = (folder or self.plugins) / (name + '.gizmo')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('Gizmo {\n name ' + name + '\n' + knobs + '}\n' + children + 'end_group\n', encoding='utf-8')
        return path

    def scan(self, **kwargs):
        return scan(self.script, plugin_paths=[self.plugins], **kwargs)

    def test_nested_gizmos_and_internal_media_are_packaged_and_verified(self):
        media = self.root / 'texture.exr'
        media.write_bytes(b'pixels')
        self.gizmo('Outer', 'Inner {\n name Inner1\n}\n')
        inner = self.gizmo('Inner', f'Read {{\n file {media.as_posix()}\n name texture\n}}\n')
        plan = self.scan()
        self.assertEqual({p.name for p in plan.plugins}, {'Outer', 'Inner'})
        self.assertTrue(all(p.status == '就绪' for p in plan.plugins))
        self.assertEqual(len(plan.assets), 1)
        destination = package(plan, self.root / 'output')
        self.assertEqual(len(list((destination / 'gizmos').glob('*.gizmo'))), 2)
        self.assertIn('[file dirname [value root.name]]/media/', (destination / 'gizmos/Inner.gizmo').read_text())
        self.assertEqual(next((destination / 'media').rglob('*.exr')).read_bytes(), b'pixels')
        report = json.loads((destination / 'report.json').read_text(encoding='utf-8'))
        self.assertTrue(report['gizmo_dependencies_complete'])
        self.assertEqual(len(report['plugins']), 2)
        self.assertTrue((destination / '打开工程.cmd').is_file())
        self.assertTrue((destination / 'load_package.py').is_file())
        for relative, digest in report['file_hashes'].items():
            self.assertEqual(hashlib.sha256((destination / relative).read_bytes()).hexdigest(), digest)
        self.assertIn(str(inner), [p.source for p in plan.plugins])

    def test_missing_plugin_blocks_package_without_creating_output(self):
        plan = self.scan()
        self.assertEqual(plan.plugins[0].status, '未找到')
        with self.assertRaisesRegex(RuntimeError, '插件'):
            package(plan, self.root / 'output')
        self.assertFalse((self.root / 'output').exists())

    def test_conflicting_versions_require_explicit_choice(self):
        self.gizmo('Outer')
        other = self.gizmo('Outer', 'Blur {\n name blur\n}\n', self.plugins / 'other')
        plan = self.scan()
        self.assertEqual(plan.plugins[0].status, '同名冲突')
        with self.assertRaises(RuntimeError):
            package(plan, self.root / 'output')
        plan = self.scan(plugin_overrides={'Outer': other})
        self.assertEqual(plan.plugins[0].source, str(other))
        self.assertEqual(plan.plugins[0].status, '就绪')

    def test_identical_duplicates_are_safe_but_changed_sources_are_blocked(self):
        first = self.gizmo('Outer')
        self.gizmo('Outer', folder=self.plugins / 'copy')
        plan = self.scan()
        self.assertEqual(plan.plugins[0].status, '就绪')
        first.write_text('Gizmo {\n name Changed\n}\nend_group\n')
        with self.assertRaisesRegex(RuntimeError, '变化'):
            package(plan, self.root / 'output')

    def test_cycles_and_nested_missing_dependencies_block_package(self):
        self.gizmo('Outer', 'Inner {\n name child\n}\n')
        self.gizmo('Inner', 'Outer {\n name loop\n}\n')
        plan = self.scan()
        self.assertTrue(any('循环' in p.note for p in plan.plugins))
        with self.assertRaises(RuntimeError):
            package(plan, self.root / 'cycle')
        (self.plugins / 'Inner.gizmo').unlink()
        plan = self.scan()
        self.assertEqual(next(p for p in plan.plugins if p.name == 'Inner').status, '未找到')
        with self.assertRaises(RuntimeError):
            package(plan, self.root / 'missing')

    def test_dynamic_internal_paths_and_callbacks_are_not_silently_accepted(self):
        self.gizmo('Outer', 'Read {\n file "[value parent.texture]"\n name tex\n}\n')
        with self.assertRaises(RuntimeError):
            package(self.scan(), self.root / 'dynamic')
        self.gizmo('Outer', knobs=' onCreate "import local_dependency"\n')
        plan = self.scan()
        self.assertEqual(plan.plugins[0].status, '待核对')
        with self.assertRaises(RuntimeError):
            package(plan, self.root / 'callback')

    def test_default_environment_paths_and_cancel(self):
        self.gizmo('Outer')
        with patch.dict(os.environ, {'NUKE_PATH': str(self.plugins)}):
            plan = scan(self.script)
        self.assertEqual(plan.plugins[0].status, '就绪')
        from core import Cancelled
        with self.assertRaises(Cancelled):
            self.scan(cancel=lambda: True)

    def test_binary_plugin_is_reported_without_claiming_gizmo_success(self):
        (self.plugins / 'Outer.dll').write_bytes(b'not a real DLL')
        plan = self.scan()
        self.assertEqual(plan.plugins[0].status, '待核对')
        self.assertIn('二进制', plan.plugins[0].note)
        with self.assertRaises(RuntimeError):
            package(plan, self.root / 'binary')

    def test_custom_file_controls_and_instance_overrides_are_collected(self):
        default = self.root / 'default.exr'
        override = self.root / 'override.exr'
        default.write_bytes(b'default')
        override.write_bytes(b'override')
        self.gizmo('Outer', knobs=f' addUserKnob {{2 texture}}\n texture {default.as_posix()}\n')
        self.script.write_text(f'Outer {{\n texture {override.as_posix()}\n name Outer1\n}}\n')
        plan = self.scan()
        self.assertEqual({a.source for a in plan.assets}, {str(default), str(override)})
        target = package(plan, self.root / 'custom')
        self.assertIn('/media/', (target / self.script.name).read_text())
        self.assertIn('/media/', (target / 'gizmos/Outer.gizmo').read_text())
        self.assertEqual({p.read_bytes() for p in (target / 'media').rglob('*.exr')}, {b'default', b'override'})
        override.unlink()
        with self.assertRaises(RuntimeError):
            package(self.scan(), self.root / 'missing_override')

    def test_linked_file_control_is_collected(self):
        media = self.root / 'override.exr'
        media.write_bytes(b'override')
        self.gizmo('Outer', 'Read {\n name Read1\n}\n', knobs=' addUserKnob {41 texture T Read1.file}\n')
        self.script.write_text(f'Outer {{\n texture {media.as_posix()}\n name Outer1\n}}\n')
        plan = self.scan()
        self.assertEqual(len(plan.assets), 1)
        self.assertEqual(plan.assets[0].source, str(media))

    def test_missing_standard_file_override_also_blocks_gizmo_delivery(self):
        self.gizmo('Outer', knobs=' addUserKnob {2 file}\n')
        self.script.write_text('Outer {\n file missing.exr\n name Outer1\n}\n')
        with self.assertRaises(RuntimeError):
            package(self.scan(), self.root / 'missing')

    def test_missing_group_closure_and_multiple_definitions_are_blocked(self):
        path = self.plugins / 'Outer.gizmo'
        for text in ['Gizmo {\n name Outer\n}\n', 'Gizmo {\n}\nend_group\nGroup {\n}\nend_group\n',
                     'Gizmo {\n}\nend_group\nRead {\n}\n', 'Gizmo {\n}\nend_group\nend_group\n',
                     'Read {\n name Read1\n}\n', 'Gizmo {']:
            with self.subTest(text=text):
                path.write_text(text)
                self.assertTrue(self.scan().plugin_blockers)

    def test_cli_failure_and_explicit_source_resolution(self):
        command = [sys.executable, '-B', str(Path(__file__).resolve().parents[1] / 'cli.py'),
                   str(self.script), '--plugin-dir', str(self.plugins)]
        result = subprocess.run(command + ['--scan'], capture_output=True)
        self.assertEqual(result.returncode, 1)
        chosen = self.gizmo('Outer')
        self.gizmo('Outer', 'Blur {\n name blur\n}\n', self.plugins / 'other')
        result = subprocess.run(command + ['--gizmo', f'Outer={chosen}', '-o', str(self.root / 'cli')], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(len(list((self.root / 'cli').rglob('Outer.gizmo'))), 1)

    def test_unreadable_search_path_and_invalid_override_are_reported(self):
        self.gizmo('Outer')
        plan = scan(self.script, plugin_paths=[self.plugins, self.root / 'missing'])
        self.assertTrue(plan.plugin_errors)
        for override in [self.root / 'missing.gizmo', self.plugins / 'Another.gizmo']:
            plan = self.scan(plugin_overrides={'Outer': override})
            self.assertEqual(plan.plugins[0].status, '待核对')

    def test_case_collisions_and_ofx_classes_are_not_silently_dropped(self):
        self.gizmo('Outer')
        self.script.write_text('Outer {\n name a\n}\nouter {\n name b\n}\nOFXcom.vendor.Plugin {\n name c\n}\n')
        plan = self.scan()
        self.assertEqual(len(plan.plugins), 3)
        self.assertEqual(plan.plugins[1].status, '同名冲突')
        self.assertEqual(plan.plugins[2].name, 'OFXcom.vendor.Plugin')

    def test_verified_standard_nodes_do_not_hide_unknown_plugin_names(self):
        names = 'BlinkScript ChannelMerge ClipTest ColorMatrix Difference Log2Lin Matrix MergeExpression Noise Position Ramp SoftClip StickyNote'.split()
        self.script.write_text(''.join(f'{name} {{\n name {name}1\n}}\n' for name in names))
        self.assertEqual(self.scan().plugins, [])
        self.script.write_text('Material {\n name custom1\n}\nLookup {\n name custom2\n}\n')
        self.assertEqual({p.name for p in self.scan().plugins}, {'Material', 'Lookup'})

    def test_package_integrity_failure_cleans_output(self):
        self.gizmo('Outer')
        plan = self.scan()
        from gizmo import finish_bundle
        def corrupt(plan, stage, edits, hashes, cancel):
            (stage / 'bad.exr').write_bytes(b'wrong')
            hashes['bad.exr'] = hashlib.sha256(b'expected').hexdigest()
            return finish_bundle(plan, stage, edits, hashes, cancel)
        with patch('gizmo.finish_bundle', side_effect=corrupt):
            with self.assertRaisesRegex(RuntimeError, '校验失败'):
                package(plan, self.root / 'corrupt')
        self.assertEqual(list((self.root / 'corrupt').iterdir()), [])

    def test_source_scripts_are_never_executed(self):
        sentinel = self.root / 'executed'
        (self.plugins / 'init.py').write_text(f'from pathlib import Path\nPath({str(sentinel)!r}).touch()\n')
        self.gizmo('Outer')
        package(self.scan(), self.root / 'safe')
        self.assertFalse(sentinel.exists())


if __name__ == '__main__':
    unittest.main()
