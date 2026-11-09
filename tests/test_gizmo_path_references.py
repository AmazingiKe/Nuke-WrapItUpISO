import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from core import DependencyError, node_blocks, package, scan
from scan_cache import ScanSession


class GizmoPathReferenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.plugins = self.root / 'registered'
        self.plugins.mkdir()
        self.script = self.root / 'shot.nk'
        defaults = patch('gizmo.default_paths', return_value=[self.plugins])
        defaults.start()
        self.addCleanup(defaults.stop)

    def definition(self, path, children='', controls=''):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('Gizmo {\n' + controls + '}\n' + children + 'end_group\n', encoding='utf-8')
        return path

    def reference(self, path, name='Glow1', controls=''):
        return '"' + path.as_posix() + '" {\n name ' + name + '\n' + controls + '}\n'

    def test_explicit_path_wins_over_same_named_search_candidate_and_is_rewritten(self):
        exact = self.definition(self.root / '中文 source/RealGlow.gizmo', controls=' label exact\n')
        self.definition(self.plugins / 'RealGlow.gizmo', controls=' label wrong\n')
        self.script.write_text(self.reference(exact), encoding='utf-8')
        original = self.script.read_bytes()
        plan = scan(self.script, automatic_plugins=True)
        self.assertEqual(len(plan.plugins), 1)
        self.assertEqual(Path(plan.plugins[0].source), exact)
        self.assertFalse(plan.script_warnings)
        target = package(plan, self.root / 'delivery')
        report = json.loads((target / 'report.json').read_text(encoding='utf-8'))
        record = report['plugins'][0]
        packed = target / record['packed_path']
        self.assertIn('label exact', packed.read_text())
        self.assertEqual(list(node_blocks((target / (self.script.stem + '_relinked_relative.nk')).read_text()))[0][0], packed.stem)
        self.assertNotIn(exact.as_posix(), (target / (self.script.stem + '_relinked_relative.nk')).read_text())
        self.assertEqual((target / report['original_path_script']).read_bytes(), original)
        self.assertEqual(self.script.read_bytes(), original)
        self.assertEqual(record['original_reference'], exact.as_posix())
        self.assertFalse(list(target.glob('*.cmd')))

    def test_nested_path_references_and_custom_file_overrides_are_collected(self):
        (self.root / 'default.exr').write_bytes(b'default')
        (self.root / 'override.exr').write_bytes(b'override')
        inner = self.definition(self.root / 'external/Inner.gizmo', controls=' addUserKnob {2 texture}\n texture default.exr\n')
        outer = self.definition(self.plugins / 'Outer.gizmo', self.reference(inner, controls=' texture override.exr\n'))
        self.script.write_text('Outer {\n}\n')
        plan = scan(self.script, automatic_plugins=True)
        self.assertFalse(plan.plugin_blockers)
        target = package(plan, self.root / 'nested')
        self.assertNotIn(inner.as_posix(), (target / 'gizmos/outer.gizmo').read_text())
        self.assertEqual({p.read_bytes() for p in (target / 'media').rglob('*.exr')}, {b'default', b'override'})
        self.assertIn('/media/', (target / 'gizmos/outer.gizmo').read_text())

    def test_same_filename_different_sources_get_distinct_names(self):
        a = self.definition(self.root / 'a/Glow.gizmo', controls=' label a\n')
        b = self.definition(self.root / 'b/Glow.gizmo', controls=' label b\n')
        self.script.write_text(self.reference(a, 'A') + self.reference(b, 'B'))
        target = package(scan(self.script, automatic_plugins=True), self.root / 'distinct')
        files = list((target / 'gizmos').glob('*.gizmo'))
        self.assertEqual(len(files), 4)
        self.assertEqual(len({p.stem.casefold() for p in files}), 4)
        report = json.loads((target / 'report.json').read_text(encoding='utf-8'))
        for script_key, class_key in (('relative_script', 'packed_class'), ('relinked_script', 'absolute_packed_class')):
            self.assertEqual({cls for cls, _, _ in node_blocks((target / report[script_key]).read_text())},
                             {p[class_key] for p in report['plugins']})

    def test_missing_explicit_path_is_reported_without_guessing_same_named_file(self):
        missing = self.root / 'absent/Glow.gizmo'
        self.definition(self.plugins / 'Glow.gizmo')
        self.script.write_text(self.reference(missing))
        plan = scan(self.script, automatic_plugins=True)
        self.assertEqual(len(plan.plugins), 1)
        self.assertEqual(plan.plugins[0].status, '未找到')
        self.assertFalse(plan.plugin_blockers)
        target = package(plan, self.root / 'missing')
        self.assertEqual((target / (self.script.stem + '_relinked_relative.nk')).read_bytes(), self.script.read_bytes())
        report = json.loads((target / 'report.json').read_text(encoding='utf-8'))
        self.assertFalse(report['gizmo_dependencies_complete'])
        self.assertEqual(report['plugins'][0]['status'], '未收集')
        with self.assertRaises(DependencyError):
            package(scan(self.script), self.root / 'strict')

    def test_same_source_by_name_and_path_does_not_duplicate_media_edits(self):
        (self.root / 'texture.exr').write_bytes(b'pixels')
        source = self.definition(self.plugins / 'CustomGlow.gizmo', 'Read {\n file texture.exr\n name Texture\n}\n')
        self.script.write_text('CustomGlow {\n}\n' + self.reference(source))
        plan = scan(self.script, automatic_plugins=True)
        self.assertEqual(len(plan.assets), 1)
        target = package(plan, self.root / 'alias')
        report = json.loads((target / 'report.json').read_text(encoding='utf-8'))
        for field in ('packed_path', 'absolute_packed_path'):
            files = [target / p[field] for p in report['plugins']]
            self.assertEqual(len(files), 2)
            self.assertEqual(files[0].read_bytes(), files[1].read_bytes())
            self.assertEqual(files[0].read_text().count('[file dirname'), 1 if field == 'packed_path' else 0)

    def test_explicit_path_changes_invalidate_scan_and_delivery(self):
        source = self.root / 'external/Glow.gizmo'
        self.script.write_text(self.reference(source))
        session = ScanSession()
        first = session.scan(self.script)
        self.assertEqual(first.plugins[0].status, '未找到')
        self.definition(source)
        updated = session.scan(self.script, reuse=True)
        self.assertIsNot(updated, first)
        self.assertEqual(updated.plugins[0].status, '就绪')
        source.write_text('Gizmo {\n label changed\n}\nend_group\n')
        with self.assertRaises(DependencyError): package(updated, self.root / 'changed')
