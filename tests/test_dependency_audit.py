import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from core import DependencyError, package, scan
from scan_cache import ScanSession


class DependencyAuditTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.plugins = self.root / 'plugins'
        self.plugins.mkdir()
        self.script = self.root / 'shot.nk'
        defaults = patch('gizmo.default_paths', return_value=[self.plugins])
        defaults.start()
        self.addCleanup(defaults.stop)

    def test_inline_file_knobs_are_scoped_per_node_and_relocated(self):
        media = self.root / 'texture.exr'
        media.write_bytes(b'pixels')
        self.script.write_text('Group {\n name Outer\n}\nGroup {\n name Embedded\n'
                               ' addUserKnob {2 texture}\n texture texture.exr\n}\nend_group\n'
                               'Group {\n name Unrelated\n addUserKnob {1 texture}\n texture plain_text\n}\nend_group\nend_group\n')
        plan = scan(self.script, automatic_plugins=True)
        self.assertEqual(len(plan.assets), 1)
        self.assertEqual(plan.assets[0].node_path, 'Outer_Embedded')
        target = package(plan, self.root / 'output')
        self.assertEqual((target / 'media/Outer_Embedded/texture.exr').read_bytes(), b'pixels')
        text = (target / (self.script.stem + '_Relinked_relative.nk')).read_text()
        self.assertIn('texture "[file dirname [value root.name]]/media/', text)
        self.assertIn('texture plain_text', text)
        media.unlink()
        plan = scan(self.script, automatic_plugins=True)
        self.assertEqual(plan.assets[0].status, '缺失')
        target = package(plan, self.root / 'missing')
        self.assertFalse(json.loads((target / 'report.json').read_text(encoding='utf-8'))['explicit_media_complete'])

    def test_custom_file_knob_on_node_inside_gizmo_is_collected(self):
        (self.root / 'texture.exr').write_bytes(b'pixels')
        self.script.write_text('Demo {\n}\n')
        (self.plugins / 'Demo.gizmo').write_text('Gizmo {\n}\nGroup {\n name Embedded\n'
                                               ' addUserKnob {2 texture}\n texture texture.exr\n}\nend_group\nend_group\n')
        plan = scan(self.script, automatic_plugins=True)
        self.assertEqual(len(plan.assets), 1)
        target = package(plan, self.root / 'nested')
        self.assertIn('/media/', (target / 'gizmos/Demo.gizmo').read_text())

    def test_outer_file_declaration_does_not_leak_into_nested_group(self):
        (self.root / 'texture.exr').write_bytes(b'pixels')
        self.script.write_text('Demo {\n}\n')
        (self.plugins / 'Demo.gizmo').write_text('Gizmo {\n addUserKnob {2 texture}\n texture texture.exr\n}\n'
                                               'Group {\n addUserKnob {1 texture}\n texture plain_text\n}\nend_group\nend_group\n')
        plan = scan(self.script, automatic_plugins=True)
        self.assertEqual(len(plan.assets), 1)
        self.assertFalse(plan.plugin_blockers)

    def test_builtin_exposure_and_mirror_nodes_are_not_missing_plugins(self):
        self.script.write_text('EXPTool {\n}\nMirror2 {\n}\nMirror {\n}\n')
        self.assertEqual(scan(self.script).plugins, [])

    def test_native_binary_dependency_is_not_copied_or_blocked(self):
        self.script.write_text('NativeTool {\n}\n')
        (self.plugins / 'NativeTool.dll').write_bytes(b'fixture')
        plan = scan(self.script, automatic_plugins=True)
        self.assertEqual(plan.plugins[0].status, '外部依赖')
        self.assertFalse(plan.plugin_blockers)
        target = package(plan, self.root / 'native')
        self.assertFalse(list(target.rglob('*.dll')))
        report = json.loads((target / 'report.json').read_text(encoding='utf-8'))
        self.assertEqual(report['plugins'][0]['status'], '外部依赖')

    def test_lower_priority_binary_does_not_block_available_gizmo(self):
        first = self.plugins / 'first'
        second = self.plugins / 'second'
        first.mkdir(); second.mkdir()
        (first / 'Demo.gizmo').write_text('Gizmo {\n}\nend_group\n')
        (second / 'Demo.dll').write_bytes(b'fixture')
        self.script.write_text('Demo {\n}\n')
        with patch('gizmo.default_paths', return_value=[first, second]):
            plan = scan(self.script, automatic_plugins=True)
        self.assertFalse(plan.plugin_blockers)
        target = package(plan, self.root / 'mixed')
        self.assertTrue((target / 'gizmos/Demo.gizmo').is_file())

    def test_same_directory_mixed_formats_remain_external_without_blocking(self):
        self.script.write_text('Demo {\n}\n')
        (self.plugins / 'Demo.gizmo').write_text('Gizmo {\n}\nend_group\n')
        (self.plugins / 'Demo.so').write_bytes(b'fixture')
        plan = scan(self.script, automatic_plugins=True)
        self.assertFalse(plan.plugin_blockers)
        self.assertEqual(plan.plugins[0].status, '外部依赖')
        self.assertEqual(len(plan.plugins[0].candidates), 2)
        target = package(plan, self.root / 'mixed_formats')
        self.assertFalse(list((target / 'gizmos').iterdir()))

    def test_deliveries_do_not_contain_launchers(self):
        self.script.write_text('Demo {\n}\n')
        (self.plugins / 'Demo.gizmo').write_text('Gizmo {\n}\nend_group\n')
        target = package(scan(self.script), self.root / 'delivery')
        for name in ('打开工程.cmd', 'launch.ps1', 'load_package.py'):
            self.assertFalse((target / name).exists())
        readme = (target / 'README.md').read_text(encoding='utf-8')
        self.assertIn('gizmos', readme)
        self.assertIn('Nuke', readme)
        self.assertNotIn('打开工程.cmd', readme)

    def test_indirect_tcl_loads_are_not_reported_verified(self):
        for command in ('set external [source helper.tcl]', 'set external [python {import helper}]',
                        'set x 1; source helper.tcl', 'if {1} {source helper.tcl}'):
            with self.subTest(command=command):
                self.script.write_text(command + '\nRoot {\n}\n')
                plan = scan(self.script, automatic_plugins=True)
                self.assertTrue(plan.plugin_blockers or plan.script_warnings)
                if plan.plugin_blockers:
                    with self.assertRaises(DependencyError): package(plan, self.root / 'indirect')
                else:
                    target = package(plan, self.root / 'indirect')
                    self.assertFalse(json.loads((target / 'report.json').read_text(encoding='utf-8'))['script_dependencies_verified'])

    def test_tcl_literals_are_not_executed_or_misidentified_as_loads(self):
        self.script.write_text('set text {[source helper.tcl]}\n# source helper.tcl\n'
                               'NoOp {\n label "[source helper.tcl]"\n}\n')
        plan = scan(self.script, automatic_plugins=True)
        self.assertFalse(plan.plugin_blockers)
        # Expressions saved on knobs still need runtime review.
        self.assertTrue(plan.script_warnings)

    def test_pathlib_directory_registration_and_cache_invalidation(self):
        shelves = self.plugins / '_gizmos'
        shelves.mkdir()
        first = shelves / 'A'
        first.mkdir()
        (first / 'Demo.gizmo').write_text('Gizmo {\n}\nend_group\n')
        hidden = shelves / '.backup'
        hidden.mkdir()
        (hidden / 'Demo.gizmo').write_text('invalid backup')
        (self.plugins / 'init.py').write_text('''from pathlib import Path
import nuke
base = Path(__file__).resolve().parent
shelves = base / '_gizmos'
if shelves.exists() and shelves.is_dir():
    for p in sorted(shelves.iterdir(), key=lambda x: x.name.lower()):
        if not p.is_dir() or p.name.startswith('.'):
            continue
        norm = p.resolve(strict=False)
        nuke.pluginAddPath(norm.as_posix())
''')
        self.script.write_text('Demo {\n}\n')
        session = ScanSession()
        plan = session.scan(self.script)
        self.assertEqual(plan.plugins[0].source, str(first / 'Demo.gizmo'))
        self.assertFalse(plan.plugin_search_errors)
        self.assertIs(session.scan(self.script, reuse=True), plan)
        second = shelves / 'B'
        second.mkdir()
        (second / 'Demo.gizmo').write_text('Gizmo {\n label other\n}\nend_group\n')
        changed = session.scan(self.script, reuse=True)
        self.assertIsNot(changed, plan)
        self.assertEqual(len(changed.plugins[0].candidates), 2)

    def test_recursive_path_helpers_are_read_statically_not_executed(self):
        shelf = self.plugins / 'shelf'
        nested = shelf / 'nested'
        nested.mkdir(parents=True)
        (nested / 'Demo.gizmo').write_text('Gizmo {\n}\nend_group\n')
        sentinel = self.root / 'executed'
        config = '''from pathlib import Path
import nuke
base = Path(__file__).resolve().parent
def register(root: Path):
    for folder in root.rglob('*'):
        if folder.is_dir():
            nuke.pluginAddPath(folder.resolve().as_posix())
def main():
    register(base)
if __name__ == '__main__':
    main()
'''
        config += f'Path({str(sentinel)!r}).write_text("must not execute")\n'
        (self.plugins / 'init.py').write_text(config)
        self.script.write_text('Demo {\n}\n')
        plan = ScanSession().scan(self.script)
        self.assertEqual(plan.plugins[0].source, str(nested / 'Demo.gizmo'))
        self.assertFalse(plan.plugin_search_errors)
        self.assertFalse(sentinel.exists())
        (self.plugins / 'init.py').write_text('''import os, nuke
dirname, filename = os.path.split(os.path.abspath(__file__))
for root, dirs, files in os.walk(dirname):
    nuke.pluginAddPath(root)
''')
        walked = ScanSession().scan(self.script)
        self.assertEqual(walked.plugins[0].source, str(nested / 'Demo.gizmo'))
        self.assertFalse(walked.plugin_search_errors)

    def test_unknown_control_flow_does_not_guess_helper_paths(self):
        (self.plugins / 'init.py').write_text('''import nuke
def register(path):
    nuke.pluginAddPath(path)
for path in ['unknown']:
    if runtime_condition():
        continue
    register(path)
''')
        self.script.write_text('Demo {\n}\n')
        plan = ScanSession().scan(self.script)
        self.assertNotIn(str(self.plugins / 'unknown'), plan.search_paths)
        self.assertTrue(plan.plugin_search_errors)

    def test_plugin_module_alias_reassignment_is_not_trusted(self):
        (self.plugins / 'init.py').write_text('''import os, nuke
os = unknown_module()
base = os.path.join('guessed', 'path')
nuke.pluginAddPath(base)
''')
        self.script.write_text('Demo {\n}\n')
        plan = ScanSession().scan(self.script)
        self.assertNotIn(str(self.plugins / 'guessed/path'), plan.search_paths)
        self.assertTrue(plan.plugin_search_errors)
