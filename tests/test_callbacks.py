import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from wrapitupiso.engine.core import DependencyError, package, scan


GLOW_CALLBACK = """
k = nuke.thisKnob()
if k.name()=='crop':
    if k.value():
        nuke.thisNode().knob('overscan').setEnabled(True)
    else:
        nuke.thisNode().knob('overscan').setEnabled(False)
"""


def quoted_callback(code):
    return '"' + code.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n') + '"'


class CallbackTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.script = self.root / 'shot.nk'
        defaults = patch('wrapitupiso.engine.gizmo.default_paths', return_value=[self.root])
        defaults.start()
        self.addCleanup(defaults.stop)

    def write_callback(self, code, quoted=True):
        value = quoted_callback(code) if quoted else '{' + code + '}'
        self.script.write_text('Group {\n name Glow_Exponential6\n knobChanged ' + value + '\n}\nend_group\n', encoding='utf-8')

    def test_glow_ui_callback_packages_without_changing_code(self):
        for automatic in (False, True):
            for quoted in (False, True):
                with self.subTest(automatic=automatic, quoted=quoted):
                    self.write_callback(GLOW_CALLBACK, quoted)
                    original = self.script.read_bytes()
                    plan = scan(self.script, automatic_plugins=automatic)
                    self.assertFalse(plan.plugin_blockers)
                    target = package(plan, self.root / f'out_{automatic}_{quoted}')
                    self.assertEqual((target / (self.script.stem + '_relinked_relative.nk')).read_bytes(), original)
                    report = json.loads((target / 'report.json').read_text(encoding='utf-8'))
                    self.assertTrue(report['gizmo_dependencies_complete'])
                    self.assertFalse(report['nuke_runtime_verified'])

    def test_ui_callback_in_nested_gizmo_is_collected(self):
        self.script.write_text('Outer {\n}\n')
        (self.root / 'Outer.gizmo').write_text('Gizmo {\n}\nInner {\n}\nend_group\n')
        inner = 'Gizmo {\n knobChanged ' + quoted_callback(GLOW_CALLBACK) + '\n}\nend_group\n'
        (self.root / 'Inner.gizmo').write_text(inner)
        plan = scan(self.script, automatic_plugins=True)
        self.assertFalse(plan.plugin_blockers)
        target = package(plan, self.root / 'nested')
        self.assertEqual((target / 'gizmos/inner.gizmo').read_text(), inner)

    def test_strict_mode_blocks_unverified_code_with_node_context(self):
        sentinel = self.root / 'executed'
        codes = [
            'import local_dependency',
            f'open({str(sentinel)!r}, "w")',
            'nuke.thisNode().knob("file").setValue("external.exr")',
            'nuke.thisNode().knob("file").execute()',
            'nuke.load("external_plugin")',
            'exec("import local_dependency")',
            'nuke = external\n' + GLOW_CALLBACK,
            'k = nuke.thisKnob()\nk = external\nk.setEnabled(True)',
            'if nuke.thisKnob().value():\n    k = nuke.thisKnob()\nk.setEnabled(True)',
            'nuke.thisNode().knob(dynamic_name).setEnabled(True)',
            'nuke.thisKnob().setEnabled(external())',
            'nuke.thisKnob().setEnabled(True, external())',
            'nuke.thisKnob().setEnabled(value=external())',
            'nuke.thisKnob().setEnabled(True)\nexternal()',
            'for k in external():\n    k.setEnabled(True)',
            'this is not valid Python!',
        ]
        for code in codes:
            with self.subTest(code=code):
                self.write_callback(code)
                plan = scan(self.script, automatic_plugins=False)
                self.assertTrue(plan.plugin_blockers)
                self.assertIn('Glow_Exponential6', '\n'.join(plan.plugin_blockers))
                with self.assertRaises(DependencyError):
                    package(plan, self.root / 'blocked')
                self.assertFalse((self.root / 'blocked').exists())
        self.assertFalse(sentinel.exists())

    def test_automatic_mode_preserves_unverified_callbacks_and_buttons_without_execution(self):
        sentinel = self.root / 'executed'
        self.write_callback(f'open({str(sentinel)!r}, "w")')
        with self.script.open('a', encoding='utf-8') as stream:
            stream.write('NoOp {\n name Controls\n'
                         ' addUserKnob {22 help T "import webbrowser; webbrowser.open_new(\'https://example.invalid/help\')"}\n'
                         ' addUserKnob {22 seed T "import random; nuke.thisNode()[\'seed\'].setValue(random.randint(1,99999))"}\n}\n')
        original = self.script.read_bytes()
        plan = scan(self.script, automatic_plugins=True)
        self.assertFalse(plan.plugin_blockers)
        self.assertEqual(len(plan.script_warnings), 3)
        for name in ('Glow_Exponential6', 'Controls', 'help', 'seed'):
            self.assertIn(name, '\n'.join(plan.script_warnings))
        target = package(plan, self.root / 'automatic')
        self.assertEqual((target / (self.script.stem + '_relinked_relative.nk')).read_bytes(), original)
        self.assertFalse(sentinel.exists())
        report = json.loads((target / 'report.json').read_text(encoding='utf-8'))
        self.assertFalse(report['script_dependencies_verified'])
        self.assertFalse(report['gizmo_dependencies_complete'])
        self.assertEqual(report['script_dependency_warnings'], plan.script_warnings)
        self.assertIn('脚本', (target / 'readme.md').read_text(encoding='utf-8'))

    def test_automatic_mode_collects_nested_gizmo_with_node_building_callback(self):
        self.script.write_text('Outer {\n}\n')
        (self.root / 'Outer.gizmo').write_text('Gizmo {\n}\nInner {\n}\nend_group\n')
        code = ('if nuke.thisKnob().name() in ["iterations"]:\n'
                '    for node in nuke.allNodes():\n'
                '        if "static" not in node["label"].getValue():\n'
                '            nuke.delete(node)\n'
                '    blur = nuke.nodes.Blur()\n'
                '    blur["size"].setExpression("parent.size")\n')
        inner = 'Gizmo {\n name Glow\n knobChanged ' + quoted_callback(code) + '\n}\nend_group\n'
        (self.root / 'Inner.gizmo').write_text(inner)
        plan = scan(self.script, automatic_plugins=True)
        self.assertFalse(plan.plugin_blockers)
        self.assertTrue(all(p.status == '就绪' for p in plan.plugins))
        self.assertIn('Inner.gizmo', '\n'.join(plan.script_warnings))
        target = package(plan, self.root / 'nested_graph')
        self.assertEqual((target / 'gizmos/inner.gizmo').read_text(), inner)
        self.assertTrue((target / 'gizmos/outer.gizmo').exists())
        # Script warnings must not suppress a confirmed missing internal asset.
        (self.root / 'Inner.gizmo').write_text(inner.replace('end_group', 'Read {\n file missing.exr\n}\nend_group'))
        broken = scan(self.script, automatic_plugins=True)
        with self.assertRaises(DependencyError):
            package(broken, self.root / 'missing')

    def test_explicit_top_level_script_load_still_blocks_automatic_packaging(self):
        self.script.write_text('source external.tcl\nRoot {\n}\n')
        plan = scan(self.script, automatic_plugins=True)
        with self.assertRaises(DependencyError):
            package(plan, self.root / 'external')

    def test_cli_packages_unverified_callback_without_running_it(self):
        sentinel = self.root / 'executed'
        self.write_callback(f'open({str(sentinel)!r}, "w")')
        result = subprocess.run(
            [sys.executable, '-X', 'utf8', str(Path(__file__).resolve().parents[1] / 'cli.py'),
             str(self.script), '--output', str(self.root / 'cli')],
            capture_output=True, encoding='utf-8',
            env=dict(os.environ, NUKE_PATH=str(self.root), USERPROFILE=str(self.root)))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('不阻止打包', result.stdout)
        self.assertFalse(sentinel.exists())
        self.assertEqual(len(list((self.root / 'cli').rglob('report.json'))), 1)

    def test_ui_aliases_and_conditions(self):
        self.write_callback('n = nuke.thisNode()\nk = nuke.thisKnob()\n'
                            'if k.name() == "crop" and not k.value():\n'
                            '    n["overscan"].setVisible(False)\n')
        self.assertFalse(scan(self.script).plugin_blockers)

    def test_missing_ofx_does_not_block_ui_callback_in_automatic_mode(self):
        self.write_callback(GLOW_CALLBACK)
        with self.script.open('a') as stream:
            stream.write('OFXcom.revisionfx.rsmb_v3 {\n name RSMB1\n}\n')
        plan = scan(self.script, automatic_plugins=True)
        self.assertFalse(plan.plugin_blockers)
        target = package(plan, self.root / 'ofx')
        report = json.loads((target / 'report.json').read_text(encoding='utf-8'))
        self.assertFalse(report['gizmo_dependencies_complete'])
        self.assertEqual(report['plugins'][0]['status'], '外部依赖')
