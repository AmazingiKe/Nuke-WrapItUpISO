import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from wrapitupiso.engine.core import package, scan
from wrapitupiso.engine.scan_cache import ScanSession


class DeliveryVariantTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.script = self.root / '中文 shot.nk'
        defaults = patch('wrapitupiso.engine.gizmo.default_paths', return_value=[])
        defaults.start()
        self.addCleanup(defaults.stop)

    def asset(self, name, content=b'asset'):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def collect(self, text, encoding='utf-8'):
        original = text.encode(encoding)
        self.script.write_bytes(original)
        target = package(scan(self.script), self.root / 'output')
        report = json.loads((target / 'report.json').read_text(encoding='utf-8'))
        self.assertEqual({p.name for p in target.glob('*.nk')}, {
            '中文 shot_original.nk', '中文 shot_relinked.nk', '中文 shot_relinked_relative.nk'})
        self.assertEqual((target / report['original_path_script']).read_bytes(), original)
        self.assertEqual(self.script.read_bytes(), original)
        absolute = (target / report['relinked_script']).read_bytes().decode(encoding)
        relative = (target / report['relative_script']).read_bytes().decode(encoding)
        for name, expected in report['file_hashes'].items():
            self.assertEqual(hashlib.sha256((target / name).read_bytes()).hexdigest(), expected)
        return target, report, absolute, relative

    def test_three_versions_preserve_bytes_and_distinguish_reference_modes(self):
        self.asset('plates/plate.0001.exr')
        self.asset('plates/plate.0002.exr')
        target, report, absolute, relative = self.collect(
            '# 中文\r\nRoot {\r\n project_directory ' + self.root.as_posix() + '\r\n}\r\n'
            'Read {\r\n file plates/plate.%04d.exr\r\n first 1\r\n last 2\r\n name Plate\r\n}\r\n', 'utf-8-sig')
        path = report['assets'][0]['packed_path']
        self.assertIn((target / path).as_posix(), absolute)
        self.assertNotIn('[file dirname', absolute)
        self.assertIn('[file dirname [value root.name]]/' + path, relative)
        self.assertTrue((target / 'fonts').is_dir())
        readme = (target / 'readme.md').read_text(encoding='utf-8')
        for name in (report['original_path_script'], report['relinked_script'], report['relative_script']):
            self.assertIn(name, readme)
        self.assertIn('绝对路径', readme)
        self.assertIn('相对路径', readme)

    def test_fonts_are_separate_deduplicated_and_different_names_cannot_overwrite(self):
        self.asset('a/shared.ttf', b'font A')
        self.asset('b/shared.ttf', b'font B')
        self.asset('c/type.pfa', b'type1')
        self.asset('c/type.afm', b'metrics')
        target, report, absolute, relative = self.collect(
            'Text {\n font a/shared.ttf\n name Title\n}\n'
            'Text {\n font b/shared.ttf\n name Subtitle\n}\n'
            'Text {\n font a/shared.ttf\n name Duplicate\n}\n'
            'NoOp {\n font_file c/type.pfa\n name Custom\n}\n')
        rows = report['assets']
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0]['packed_path'], rows[2]['packed_path'])
        self.assertNotEqual(rows[0]['packed_path'], rows[1]['packed_path'])
        self.assertEqual(report['copied_files'], 4)
        for row in rows:
            self.assertEqual(row['kind'], 'font')
            self.assertTrue(row['packed_path'].startswith('fonts/'))
            self.assertIn((target / row['packed_path']).as_posix(), absolute)
            self.assertIn('[file dirname [value root.name]]/' + row['packed_path'], relative)
        self.assertEqual((target / rows[0]['packed_path']).read_bytes(), b'font A')
        self.assertEqual((target / rows[1]['packed_path']).read_bytes(), b'font B')
        self.assertEqual(len(list((target / 'fonts').rglob('*.afm'))), 1)

    def test_text2_family_fonts_use_project_font_path_in_both_versions(self):
        self.asset('project fonts/My Font.ttf', b'font')
        target, report, absolute, relative = self.collect(
            'Root {\n free_type_font_path {project fonts}\n}\n'
            'Text2 {\n font {{ My Family : Regular : My Font.ttf : 0 }}\n name Title\n}\n')
        row = report['assets'][0]
        self.assertEqual(row['status'], '就绪')
        self.assertEqual((target / row['packed_path']).read_bytes(), b'font')
        self.assertIn('My Family : Regular', relative)
        self.assertIn('free_type_font_path "' + (target / 'fonts').as_posix(), absolute)
        self.assertIn('free_type_font_path "[file dirname [value root.name]]/fonts', relative)

    def test_missing_fonts_are_reported_without_replacing_the_original_reference(self):
        target, report, absolute, relative = self.collect('Text {\n font missing.ttf\n name Title\n}\n')
        self.assertEqual(report['assets'][0]['status'], '缺失')
        self.assertFalse(report['explicit_media_complete'])
        self.assertIn('font missing.ttf', absolute)
        self.assertIn('font missing.ttf', relative)
        self.assertIn('未成功', (target / 'readme.md').read_text(encoding='utf-8'))

    def test_font_changes_invalidate_scan_cache(self):
        font = self.asset('fonts/font.ttf', b'first')
        self.script.write_text('Text {\n font fonts/font.ttf\n name Title\n}\n')
        session = ScanSession()
        first = session.scan(self.script)
        self.assertEqual(first.assets[0].files, [font])
        self.assertIs(session.scan(self.script, reuse=True), first)
        font.write_bytes(b'changed font')
        self.assertIsNot(session.scan(self.script, reuse=True), first)

    def test_multiple_text2_fonts_and_default_font_without_root(self):
        for name in ('UtopiaRegular.pfa', 'a.ttf', 'b.otf'):
            self.asset('fonts/' + name)
        target, report, absolute, relative = self.collect(
            'Text2 {\n name Default\n}\nText2 {\n name Mixed\n'
            ' font {{ First Family : Regular : a.ttf : 0 } { Second Family : Bold : b.otf : 1 }}\n}\n')
        self.assertEqual(len(report['assets']), 3)
        self.assertTrue(all(row['status'] == '就绪' for row in report['assets']))
        self.assertIn('font {{ Utopia : Regular : utopiaregular.pfa : 0 }}', relative)
        self.assertIn('Second Family : Bold : b.otf : 1', relative)
        self.assertLess(relative.index('knob root.free_type_font_path'), relative.index('Text2 {'))
        self.assertNotIn('knob root.free_type_font_path', absolute)

    def test_dynamic_and_unparseable_font_references_remain_review_items(self):
        _, report, absolute, relative = self.collect(
            'Text {\n font {[value custom_font]}\n name Dynamic\n}\n'
            'Text2 {\n font {unsupported serialized data}\n name Unknown\n}\n')
        self.assertEqual([row['status'] for row in report['assets']], ['待处理', '待处理'])
        self.assertTrue(all(row['kind'] == 'font' for row in report['assets']))
        self.assertIn('[value custom_font]', relative)
        self.assertIn('unsupported serialized data', absolute)

    def test_type1_companions_keep_matching_stems_when_font_names_collide(self):
        for folder in ('a', 'b'):
            self.asset(folder + '/type.pfa', folder.encode())
            self.asset(folder + '/type.afm', b'metrics')
        target, report, _, _ = self.collect('Text {\n font a/type.pfa\n}\nText {\n font b/type.pfa\n}\n')
        for row in report['assets']:
            self.assertTrue((target / row['packed_path']).with_suffix('.afm').is_file())

    def test_nuke_font_directory_limitation_is_visible_in_report_and_readme(self):
        self.asset('fonts/font.ttf')
        target, report, _, _ = self.collect('Text2 {\n font {{ Test : Regular : font.ttf : 0 }}\n}\n')
        self.assertIn('Nuke 15.1v5', ' '.join(report['font_warnings']))
        self.assertIn('英文', (target / 'readme.md').read_text(encoding='utf-8'))

    def test_text2_finds_registered_font_directory_without_gizmo_nodes(self):
        plugin_root = self.root / 'plugins'
        font = self.asset('registered/font.ttf')
        self.asset('plugins/init.py', b"import nuke\nnuke.pluginAddPath('../registered')\n")
        with patch('wrapitupiso.engine.gizmo.default_paths', return_value=[plugin_root]):
            target, report, _, _ = self.collect('Text2 {\n font {{ Test : Regular : font.ttf : 0 }}\n}\n')
        self.assertEqual(report['assets'][0]['status'], '就绪')
        self.assertEqual((target / report['assets'][0]['packed_path']).read_bytes(), font.read_bytes())

    def test_absolute_gizmo_alias_cannot_overwrite_an_existing_relative_class(self):
        self.asset('plugins/Demo.gizmo', b'Gizmo {\n}\nend_group\n')
        self.asset('plugins/Demo_relinked.gizmo', b'Gizmo {\n label other\n}\nend_group\n')
        with patch('wrapitupiso.engine.gizmo.default_paths', return_value=[self.root / 'plugins']):
            target, report, absolute, relative = self.collect('Demo {\n}\nDemo_relinked {\n}\n')
        paths = [p[key] for p in report['plugins'] for key in ('packed_path', 'absolute_packed_path')]
        self.assertEqual(len(set(paths)), 4)
        self.assertEqual(len(list((target / 'gizmos').glob('*.gizmo'))), 4)
        self.assertIn('demo_relinked_2 {', absolute)
        self.assertIn('demo_relinked {', relative)


if __name__ == '__main__':
    unittest.main()
