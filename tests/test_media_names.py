import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from core import scan, package


class MediaFolderTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.script = self.root / 'shot.nk'
        defaults = patch('gizmo.default_paths', return_value=[])
        defaults.start()
        self.addCleanup(defaults.stop)

    def media(self, path, content=b'pixels'):
        file = self.root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(content)

    def read(self, name, path, extra=''):
        return f'Read {{\n name {{{name}}}\n file {path}\n{extra}}}\n'

    def collect(self, text):
        self.script.write_text(text, encoding='utf-8')
        target = package(scan(self.script), self.root / 'output')
        report = json.loads((target / 'report.json').read_text(encoding='utf-8'))
        # Every rewritten reference resolves, including sequence members; no duplicate copies.
        plan = scan(self.script)
        for asset, row in zip(plan.assets, report['assets']):
            if not asset.files:
                self.assertNotIn('packed_path', row)
                continue
            folder = target / Path(row['packed_path']).parent
            for source in asset.files:
                self.assertEqual((folder / source.name.lower()).read_bytes(), source.read_bytes())
            self.assertIn(row['packed_path'], (target / (self.script.stem + '_relinked_relative.nk')).read_text(encoding='utf-8'))
        self.assertEqual(report['copied_files'], len(list(plan.media_files())))
        self.assertEqual(len([p for p in (target / 'media').rglob('*') if p.is_file()]), report['copied_files'])
        return target, report

    def test_distinct_assets_in_same_source_directory_use_their_nodes(self):
        self.media('source/plate.exr')
        self.media('source/mask.exr')
        target, report = self.collect(self.read('Read_Plate', 'source/plate.exr') + self.read('Read_Mask', 'source/mask.exr'))
        self.assertEqual([r['packed_path'] for r in report['assets']],
                         ['media/read_plate/plate.exr', 'media/read_mask/mask.exr'])

    def test_nested_groups_and_end_group_scope(self):
        for i in range(4):
            self.media(f'{i}/plate.exr', bytes([i]))
        text = ('Group {\n name A\n}\nGroup {\n name B\n}\n' + self.read('Read1', '0/plate.exr')
                + 'end_group\n' + self.read('Read1', '1/plate.exr') + 'end_group\n'
                + 'Group {\n name C\n}\n' + self.read('Read1', '2/plate.exr') + 'end_group\n'
                + self.read('Read1', '3/plate.exr'))
        target, report = self.collect(text)
        self.assertEqual([Path(r['packed_path']).parent.name for r in report['assets']],
                         ['a_b_read1', 'a_read1', 'c_read1', 'read1'])

    def test_shared_frames_and_overlapping_patterns_use_one_folder(self):
        for frame in (1, 2, 10000):
            self.media(f'plate.{frame:04d}.exr', str(frame).encode())
        text = (self.read('Single', 'plate.0001.exr') + self.read('OtherSingle', 'plate.0002.exr')
                + self.read('Sequence', 'plate.%04d.exr', ' first 1\n last 2\n')
                + self.read('Duplicate', 'plate.####.exr', ' first 1\n last 2\n'))
        target, report = self.collect(text)
        self.assertEqual({Path(r['packed_path']).parent.name for r in report['assets']}, {'single'})
        self.assertEqual(report['copied_files'], 3)

    def test_case_and_sanitization_collisions_cannot_overwrite_files(self):
        names = ['Read1', 'read1', 'Read1_2', 'Read/Plate', 'Read:Plate', 'CON', '../..', '中文', 'x' * 200]
        text = ''
        for i, name in enumerate(names):
            self.media(f'{i}/same.exr', bytes([i]))
            text += self.read(name, f'{i}/same.exr')
        target, report = self.collect(text)
        folders = [Path(r['packed_path']).parent.name for r in report['assets']]
        self.assertEqual(folders[0], 'read1')
        self.assertEqual(folders[1], 'read1_2')
        self.assertEqual(len(set(s.casefold() for s in folders)), len(names))
        for folder in folders:
            self.assertRegex(folder, r'^[A-Za-z0-9_-]+$')
            self.assertLessEqual(len(folder), 100)
            self.assertNotEqual(folder.upper(), 'CON')

    def test_file_and_proxy_with_same_basename_remain_separate(self):
        self.media('full/image.exr', b'full')
        self.media('proxy/image.exr', b'proxy')
        target, report = self.collect(self.read('Read1', 'full/image.exr', ' proxy proxy/image.exr\n'))
        self.assertEqual(report['assets'][0]['packed_path'], 'media/read1/image.exr')
        self.assertEqual(report['assets'][1]['packed_path'], 'media/read1_2/image.exr')

    def test_missing_media_does_not_reserve_a_directory(self):
        self.media('good.exr')
        target, report = self.collect(self.read('Read1', 'missing.exr') + self.read('Read1', 'good.exr'))
        self.assertEqual(report['assets'][1]['packed_path'], 'media/read1/good.exr')
