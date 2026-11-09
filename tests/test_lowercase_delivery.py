import hashlib
import json
from pathlib import Path, PurePosixPath
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from wrapitupiso.engine.core import node_blocks, package, scan
from wrapitupiso.engine.font_scan import font_destinations


class LowercaseDeliveryTests(unittest.TestCase):
    def test_all_output_names_and_references_are_lowercase(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'Source'
            source.mkdir()
            for name in ('Plate.0001.EXR', 'Plate.0002.EXR', 'Tile.1001.EXR', 'Title.TTF'):
                (source / name).write_bytes(name.encode())
            (source / 'Inner.gizmo').write_text(
                'Gizmo {\n}\nRead {\n file Plate.0001.EXR\n name InternalRead\n}\nend_group\n')
            (source / 'Outer.gizmo').write_text('Gizmo {\n}\nInner {\n name Nested\n}\nend_group\n')
            script = source / 'MixedShot.NK'
            original = ('Root {\n}\nRead {\n file Plate.%04d.EXR\n first 1\n last 2\n name MainRead\n}\n'
                        'Read {\n file Tile.<UDIM>.EXR\n name Tiles\n}\n'
                        'Text {\n font Title.TTF\n name Heading\n}\nOuter {\n name MyGizmo\n}\n')
            script.write_bytes(original.encode())
            with patch('wrapitupiso.engine.gizmo.default_paths', return_value=[source]):
                target = package(scan(script), root / 'Delivery')
            names = {p.relative_to(target).as_posix() for p in target.rglob('*')}
            self.assertEqual(target.name, target.name.lower())
            self.assertEqual(names, {name.lower() for name in names})
            self.assertTrue({'mixedshot_original.nk', 'mixedshot_relinked.nk',
                             'mixedshot_relinked_relative.nk', 'readme.md', 'fonts/title.ttf'} <= names)
            report = json.loads((target / 'report.json').read_text(encoding='utf-8'))
            self.assertEqual((target / report['original_path_script']).read_bytes(), original.encode())
            self.assertEqual(script.read_bytes(), original.encode())
            for key in ('relinked_script', 'relative_script'):
                text = (target / report[key]).read_text()
                self.assertIn('plate.%04d.exr', text)
                self.assertIn('tile.<UDIM>.exr', text)  # Nuke's token is case-sensitive.
                self.assertIn('name MyGizmo', text)
            for row in report['assets']:
                packed = row['packed_path']
                self.assertEqual(packed.replace('<UDIM>', ''), packed.replace('<UDIM>', '').lower())
                self.assertIn(packed.replace('%04d', '0001').replace('<UDIM>', '1001'), names)
            for script_key, class_key, path_key in (
                    ('relative_script', 'packed_class', 'packed_path'),
                    ('relinked_script', 'absolute_packed_class', 'absolute_packed_path')):
                classes = {p[class_key] for p in report['plugins']}
                documents = [(target / report[script_key]).read_text()]
                for plugin in report['plugins']:
                    self.assertEqual(Path(plugin[path_key]).stem, plugin[class_key])
                    documents.append((target / plugin[path_key]).read_text())
                references = {cls for text in documents for cls, _, _ in node_blocks(text)}
                self.assertTrue(classes <= references)
                self.assertFalse({'Inner', 'Outer'} & references)
            for name, digest in report['file_hashes'].items():
                self.assertIn(name, names)
                self.assertEqual(hashlib.sha256((target / name).read_bytes()).hexdigest(), digest)

    def test_text2_records_and_type1_companions_match_lowercase_names(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fonts = root / 'fonts'
            fonts.mkdir()
            for name in ('UtopiaRegular.pfa', 'UtopiaRegular.afm', 'MyFont.TTF'):
                (fonts / name).write_bytes(name.encode())
            script = root / 'Title.NK'
            script.write_text('Text2 {\n name Default\n}\nText2 {\n name Custom\n'
                              ' font {{ My Family : Regular : MyFont.TTF : 0 }}\n}\n')
            with patch('wrapitupiso.engine.gizmo.default_paths', return_value=[]):
                target = package(scan(script), root / 'output')
            names = {p.name for p in (target / 'fonts').iterdir()}
            self.assertEqual(names, {'utopiaregular.pfa', 'utopiaregular.afm', 'myfont.ttf'})
            for script in target.glob('*relinked*.nk'):
                text = script.read_text()
                self.assertIn('Utopia : Regular : utopiaregular.pfa', text)
                self.assertIn('My Family : Regular : myfont.ttf', text)
                self.assertIn('/fonts', text)

    def test_font_name_collisions_keep_distinct_files_and_companion_stems(self):
        paths = [PurePosixPath(path) for path in
                 ('/a/Type.PFA', '/a/Type.AFM', '/b/type.pfa', '/b/type.afm')]
        destinations = font_destinations([SimpleNamespace(kind='font', files=paths)])
        self.assertEqual(len(set(destinations.values())), 4)
        self.assertTrue(all(name == name.lower() for name in destinations.values()))
        self.assertEqual(Path(destinations[paths[0]]).stem, Path(destinations[paths[1]]).stem)
        self.assertEqual(Path(destinations[paths[2]]).stem, Path(destinations[paths[3]]).stem)


if __name__ == '__main__':
    unittest.main()
