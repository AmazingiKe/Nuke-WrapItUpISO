import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from core import Cancelled, scan, package
from plugin_paths import discover_paths


class PluginPathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.seed = self.root / 'startup'; self.seed.mkdir()

    def write(self, folder, text):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'init.py').write_text(text, encoding='utf-8')

    def test_relative_nested_paths_cycles_and_duplicates(self):
        first, second = self.root / '库 A', self.root / '库 B'
        self.write(self.seed, "import nuke\nnuke.pluginAddPath('../库 A')\nnuke.pluginAddPath('../库 A')")
        self.write(first, "import nuke\nnuke.pluginAppendPath('../库 B')")
        self.write(second, "import nuke\nnuke.pluginAddPath('../startup')")
        result = discover_paths([self.seed])
        self.assertEqual(set(result.paths), {self.seed, first, second})
        self.assertEqual(len(result.paths), 3)
        self.assertFalse(result.errors)

    def test_variables_environment_join_file_and_literal_loop(self):
        self.write(self.seed, '''import os as system
from nuke import pluginAddPath as add
base = system.path.dirname(__file__)
for folder in ['a', 'b']:
    add(system.path.join(base, folder))
add(system.path.join(system.environ['TEST_PLUGIN_BASE'], 'external'))
''')
        with patch.dict(os.environ, {'TEST_PLUGIN_BASE': str(self.root)}):
            result = discover_paths([self.seed])
        self.assertEqual(set(result.paths), {self.seed, self.seed/'a', self.seed/'b', self.root/'external'})
        self.assertFalse(result.errors)

    def test_unknown_condition_dynamic_call_and_missing_env_are_reported(self):
        self.write(self.seed, '''import nuke, os
if nuke.env['gui']:
    nuke.pluginAddPath('conditional')
nuke.pluginAddPath(find_plugins())
nuke.pluginAddPath(os.environ['ABSENT_TEST_PLUGIN_ENV'])
''')
        with patch.dict(os.environ, {}, clear=True):
            result = discover_paths([self.seed])
        self.assertEqual(result.paths, [self.seed])
        self.assertEqual(len(result.errors), 3)
        self.assertTrue(all('init.py:' in message for message in result.errors))

    def test_no_source_code_exec_and_unknown_assignment_invalidates_old_value(self):
        sentinel = self.root / 'executed'
        self.write(self.seed, f'''from pathlib import Path
import nuke
Path({str(sentinel)!r}).touch()
p = 'old'
p = unknown()
nuke.pluginAddPath(p)
''')
        result = discover_paths([self.seed])
        self.assertFalse(sentinel.exists())
        self.assertNotIn(self.seed/'old', result.paths)
        self.assertEqual(len(result.errors), 1)

    def test_syntax_errors_and_cancel(self):
        self.write(self.seed, 'nuke.pluginAddPath(')
        self.assertTrue(discover_paths([self.seed]).errors)
        with self.assertRaises(Cancelled):
            discover_paths([self.seed], cancel=lambda: True)

    def test_constant_branches_fstrings_and_path_priority(self):
        self.write(self.seed, '''import nuke, os
base = os.path.dirname(os.path.realpath(__file__))
if False:
    nuke.pluginAddPath('unused')
else:
    nuke.pluginAddPath(base + '/first')
name = 'last'
nuke.pluginAppendPath(f'{base}/{name}')
''')
        result = discover_paths([self.seed])
        self.assertEqual(result.paths, [self.seed/'first', self.seed, self.seed/'last'])
        self.assertFalse(result.errors)

    def test_loop_control_does_not_invent_paths_and_parse_budget_is_bounded(self):
        self.write(self.seed, "import nuke\nfor name in ['a', 'b']:\n    break\n    nuke.pluginAddPath(name)\n")
        result = discover_paths([self.seed])
        self.assertEqual(result.paths, [self.seed])
        self.assertFalse(result.errors)
        self.write(self.seed, 'value = 1\n' * 10001)
        self.assertIn('步数限制', ' '.join(discover_paths([self.seed]).errors))

    def test_unset_expandvars_and_oversized_config_are_reported(self):
        self.write(self.seed, "import nuke, os\nnuke.pluginAddPath(os.path.expandvars('$UNSET_PLUGIN_TEST_ROOT/x'))")
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(discover_paths([self.seed]).errors)
        self.write(self.seed, '#' * (1024 * 1024 + 1))
        self.assertIn('1 MiB', ' '.join(discover_paths([self.seed]).errors))

    def test_default_scan_discovers_and_packages_external_gizmo(self):
        external = self.root / 'external'
        external.mkdir()
        (external / 'Demo.gizmo').write_text('Gizmo {\n name Demo\n}\nend_group\n')
        self.write(self.seed, "import nuke\nnuke.pluginAddPath('../external')")
        script = self.root / 'shot.nk'
        script.write_text('Root {\n}\nDemo {\n name Demo1\n}\n')
        with patch('gizmo.default_paths', return_value=[self.seed]):
            plan = scan(script)
        self.assertEqual(plan.plugins[0].status, '就绪')
        self.assertIn(str(external), plan.search_paths)
        destination = package(plan, self.root / 'out')
        self.assertTrue((destination / 'gizmos/Demo.gizmo').is_file())


class AutomaticPluginTests(unittest.TestCase):
    def test_missing_plugin_is_recorded_without_blocking_other_files(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            script = folder / 'shot.nk'
            script.write_text('Root {\n}\nMissingGizmo {\n name m\n}\n')
            with patch('gizmo.default_paths', return_value=[]):
                plan = scan(script, automatic_plugins=True)
            self.assertFalse(plan.plugin_blockers)
            target = package(plan, folder / 'out')
            import json
            report = json.loads((target/'report.json').read_text(encoding='utf-8'))
            self.assertFalse(report['gizmo_dependencies_complete'])
            self.assertEqual(report['plugins'][0]['status'], '未收集')
            self.assertFalse(report['plugins'][0]['packed_path'])

    def test_same_name_uses_first_search_directory_and_records_choice(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            a, b = folder/'first', folder/'second'
            a.mkdir(); b.mkdir()
            for directory, label in ((a, 'first'), (b, 'second')):
                (directory/'Demo.gizmo').write_text('Gizmo {\n name Demo\n label '+label+'\n}\nend_group\n')
            script = folder/'shot.nk'; script.write_text('Demo {\n name d\n}\n')
            with patch('gizmo.default_paths', return_value=[a,b]):
                plan = scan(script, automatic_plugins=True)
            self.assertEqual(plan.plugins[0].source, str(a/'Demo.gizmo'))
            self.assertFalse(plan.plugin_blockers)
            self.assertTrue(any('自动选择' in note for note in plan.warnings))
            target = package(plan, folder/'out')
            self.assertIn('first', (target/'gizmos/Demo.gizmo').read_text())

    def test_nested_missing_plugin_keeps_available_parent(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            (folder/'Outer.gizmo').write_text('Gizmo {\n name Outer\n}\nMissing {\n name child\n}\nend_group\n')
            script=folder/'shot.nk'; script.write_text('Outer {\n name outer\n}\n')
            with patch('gizmo.default_paths', return_value=[folder]):
                plan=scan(script,automatic_plugins=True)
            target=package(plan,folder/'out')
            self.assertTrue((target/'gizmos/Outer.gizmo').exists())
            self.assertFalse((target/'gizmos/Missing.gizmo').exists())


if __name__ == '__main__':
    unittest.main()
