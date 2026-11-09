import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from wrapitupiso.config.i18n import status_code
from wrapitupiso.engine.core import Cancelled, package, scan
from wrapitupiso.engine.relink import dependency_key, find_candidates, manual_path
from wrapitupiso.engine.scan_cache import ScanSession


class RelinkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.script = self.root / 'shot.nk'
        self.search = self.root / 'search'
        self.search.mkdir()
        defaults = patch('wrapitupiso.engine.gizmo.default_paths', return_value=[])
        defaults.start()
        self.addCleanup(defaults.stop)

    def file(self, name, content=b'pixels'):
        path = self.search / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def project(self, raw='lost/plate.exr', extra=''):
        self.script.write_text(f'Root {{\n}}\nRead {{\n file {raw}\n first 1001\n last 1003\n name Plate\n}}\n' + extra, encoding='utf-8')
        return scan(self.script, automatic_plugins=True)

    def font_file(self):
        from fontTools.ttLib import TTFont, newTable
        path = self.search / 'NewFont.ttf'
        font = TTFont()
        font['name'] = newTable('name')
        font['name'].names = []
        font['name'].setName('Replacement Family', 1, 3, 1, 0x409)
        font['name'].setName('Bold', 2, 3, 1, 0x409)
        font.save(path)
        font.close()
        return path

    def test_manual_media_replacement_packages_new_source_preserving_original(self):
        plan = self.project()
        original = self.script.read_bytes()
        target = self.file('new folder/replacement.exr')
        choices = {dependency_key(plan.assets[0]): manual_path(plan.assets[0], target)}
        repaired = scan(self.script, automatic_plugins=True, replacements=choices)
        asset = repaired.assets[0]
        self.assertEqual(asset.source, str(target))
        self.assertEqual(asset.raw, 'lost/plate.exr')
        self.assertEqual(status_code(asset.status), 'ready')
        output = package(repaired, self.root / 'delivery')
        self.assertEqual(next((output / 'media').rglob('*.exr')).read_bytes(), b'pixels')
        self.assertIn('replacement.exr', (output / 'shot_relinked_relative.nk').read_text())
        self.assertEqual(self.script.read_bytes(), original)
        self.assertEqual((output / 'shot_original.nk').read_bytes(), original)
        report = json.loads((output / 'report.json').read_text(encoding='utf-8'))
        self.assertEqual(report['assets'][0]['replacement_source'], str(target))

    def test_manual_sequence_keeps_pattern_and_reports_missing_frames(self):
        plan = self.project('lost/plate.####.exr')
        frame = self.file('new/new_plate.1001.exr')
        self.file('new/new_plate.1003.exr')
        choice = manual_path(plan.assets[0], frame)
        self.assertEqual(Path(choice).name, 'new_plate.####.exr')
        repaired = scan(self.script, replacements={dependency_key(plan.assets[0]): choice})
        self.assertEqual(len(repaired.assets[0].files), 2)
        self.assertEqual(repaired.assets[0].missing, [1002])
        self.assertEqual(status_code(repaired.assets[0].status), 'missing_frames')

    def test_recursive_search_groups_sequence_and_does_not_choose_ambiguous_files(self):
        plan = self.project('lost/Plate.%04d.exr', 'Read {\n file lost/logo.png\n name Logo\n}\n')
        for number in (1001, 1002, 1003): self.file(f'deep/nested/plate.{number}.exr')
        self.file('a/logo.png')
        self.file('b/logo.png', b'other')
        matches, errors = find_candidates(plan.assets, self.search)
        self.assertFalse(errors)
        sequence = matches[dependency_key(plan.assets[0])]
        self.assertEqual(sequence, [str(self.search / 'deep/nested/plate.%04d.exr')])
        self.assertEqual(len(matches[dependency_key(plan.assets[1])]), 2)
        repaired = scan(self.script, replacements={dependency_key(plan.assets[0]): sequence[0]})
        self.assertEqual(len(repaired.assets[0].files), 3)
        self.assertEqual(status_code(repaired.assets[0].status), 'ready')

    def test_udim_search_preserves_token_and_all_tiles(self):
        plan = self.project('lost/skin.<UDIM>.exr')
        first = self.file('skin/skin.1001.exr')
        self.file('skin/skin.1011.exr')
        self.assertEqual(Path(manual_path(plan.assets[0], first)).name, 'skin.<UDIM>.exr')
        matches, _ = find_candidates(plan.assets, self.search)
        choices = {dependency_key(plan.assets[0]): matches[dependency_key(plan.assets[0])][0]}
        repaired = scan(self.script, replacements=choices)
        self.assertEqual(len(repaired.assets[0].files), 2)
        self.assertFalse(repaired.assets[0].missing)

    def test_chosen_gizmo_collects_nested_dependencies_and_internal_media(self):
        plan = self.project(extra='MissingTool {\n name Custom\n}\n')
        gizmo = self.file('plugins/renamed.gizmo', b'Gizmo {\n}\nChild {\n name Nested\n}\nend_group\n')
        texture = self.file('texture.exr')
        self.file('plugins/Child.gizmo', f'Gizmo {{\n}}\nRead {{\n file {texture.as_posix()}\n name Texture\n}}\nend_group\n'.encode())
        choices = {dependency_key(plan.plugins[0]): str(gizmo)}
        repaired = scan(self.script, automatic_plugins=True, replacements=choices)
        self.assertEqual({p.name for p in repaired.plugins}, {'MissingTool', 'Child'})
        self.assertTrue(all(status_code(p.status) == 'ready' for p in repaired.plugins))
        self.assertEqual(repaired.plugins[0].source, str(gizmo))
        output = package(repaired, self.root / 'delivery')
        self.assertTrue((output / 'gizmos/missingtool.gizmo').is_file())
        self.assertTrue((output / 'gizmos/child.gizmo').is_file())
        self.assertIn('missingtool', (output / 'shot_relinked_relative.nk').read_text())

    def test_repairing_gizmo_internal_media_clears_dependency_blocker(self):
        self.script.write_text('Tool {\n name Tool1\n}\n')
        gizmo = self.file('Tool.gizmo', b'Gizmo {\n}\nRead {\n file lost/internal.exr\n name Texture\n}\nend_group\n')
        choices = {('plugin', 'Tool'): str(gizmo)}
        plan = scan(self.script, automatic_plugins=True, replacements=choices)
        self.assertTrue(plan.plugin_blockers)
        texture = self.file('new/internal.exr')
        choices[dependency_key(plan.assets[0])] = str(texture)
        repaired = scan(self.script, automatic_plugins=True, replacements=choices)
        self.assertFalse(repaired.plugin_blockers)
        self.assertEqual(status_code(repaired.plugins[0].status), 'ready')

    def test_search_finds_gizmo_candidates_but_keeps_binaries_external(self):
        plan = self.project(extra='Tool {\n}\nOFXcom.vendor.effect {\n}\n')
        self.file('a/Tool.gizmo', b'Gizmo {\n}\nend_group\n')
        self.file('b/Tool.gizmo', b'Gizmo {\n name Other\n}\nend_group\n')
        matches, _ = find_candidates(plan.plugins, self.search)
        self.assertEqual(len(matches[dependency_key(plan.plugins[0])]), 2)
        binary = self.file('vendor/effect.ofx', b'binary')
        repaired = scan(self.script, automatic_plugins=True, replacements={dependency_key(plan.plugins[1]): str(binary)})
        self.assertEqual(status_code(repaired.plugins[1].status), 'external')
        self.assertEqual(repaired.plugins[1].source, str(binary))

    def test_replacement_survives_cache_reuse_and_source_invalidation(self):
        self.project()
        session = ScanSession()
        plan = session.scan(self.script)
        target = self.file('replacement.exr')
        choices = {dependency_key(plan.assets[0]): str(target)}
        repaired = session.scan(self.script, replacements=choices, reuse=True)
        self.assertIsNot(repaired, plan)
        self.assertIs(session.scan(self.script, replacements=choices, reuse=True), repaired)
        target.write_bytes(b'updated')
        refreshed = session.scan(self.script, replacements=choices, reuse=True)
        self.assertIsNot(refreshed, repaired)
        self.assertEqual(refreshed.assets[0].source, str(target))
        restored = session.scan(self.script, replacements={}, reuse=True)
        self.assertEqual(status_code(restored.assets[0].status), 'missing')

    def test_font_replacement_is_collected_and_packaged(self):
        self.script.write_text('Text2 {\n font {{ Example : Regular : Missing.ttf : 0 }}\n name Title\n}\n')
        with patch('wrapitupiso.engine.font_scan.search_folders', return_value=([], [])):
            plan = scan(self.script)
            font = self.font_file()
            repaired = scan(self.script, replacements={dependency_key(plan.assets[0]): str(font)})
        self.assertEqual(status_code(repaired.assets[0].status), 'ready')
        output = package(repaired, self.root / 'delivery')
        self.assertTrue((output / 'fonts/newfont.ttf').is_file())
        self.assertIn('Replacement Family : Bold : newfont.ttf : 0', (output / 'shot_relinked_relative.nk').read_text())

    def test_manual_font_can_repair_an_unreadable_text2_font_record(self):
        self.script.write_text('Text2 {\n font {invalid}\n name Title\n}\n')
        with patch('wrapitupiso.engine.font_scan.search_folders', return_value=([], [])):
            plan = scan(self.script)
            font = self.font_file()
            repaired = scan(self.script, replacements={dependency_key(plan.assets[0]): str(font)})
        self.assertEqual(status_code(repaired.assets[0].status), 'ready')
        output = package(repaired, self.root / 'delivery')
        script = (output / 'shot_relinked_relative.nk').read_text()
        self.assertIn('newfont.ttf : 0', script)
        self.assertNotIn('font {invalid}', script)
        self.assertIn('Replacement Family : Bold', script)

    def test_invalid_font_replacement_stays_unresolved(self):
        self.script.write_text('Text2 {\n font {{ Example : Regular : Missing.ttf : 0 }}\n name Title\n}\n')
        with patch('wrapitupiso.engine.font_scan.search_folders', return_value=([], [])):
            plan = scan(self.script)
            invalid = self.file('broken.ttf', b'not a font')
            repaired = scan(self.script, replacements={dependency_key(plan.assets[0]): str(invalid)})
        self.assertEqual(status_code(repaired.assets[0].status), 'pending')
        self.assertFalse(repaired.assets[0].files)

    def test_search_can_cancel_without_changing_plan(self):
        plan = self.project()
        with self.assertRaises(Cancelled): find_candidates(plan.assets, self.search, cancel=lambda: True)
        self.assertFalse(plan.replacements)

    def test_missing_search_folder_and_wrong_file_type_are_rejected(self):
        plan = self.project(extra='Tool {\n}\n')
        with self.assertRaises(FileNotFoundError): find_candidates(plan.assets, self.root / 'absent')
        with self.assertRaises(ValueError): manual_path(plan.plugins[0], self.file('bad.txt'))

    def test_new_frame_padding_and_hyphen_separator_are_preserved(self):
        plan = self.project('lost/plate.%04d.exr')
        selected = self.file('new/plate-001001.exr')
        self.assertEqual(Path(manual_path(plan.assets[0], selected)).name, 'plate-%06d.exr')
        self.file('found/plate.001001.exr')
        matches, _ = find_candidates(plan.assets, self.search)
        self.assertEqual(matches[dependency_key(plan.assets[0])], [str(self.search / 'found/plate.%06d.exr')])

    def test_stale_replacement_does_not_affect_changed_source_knob(self):
        plan = self.project()
        replacement = self.file('replacement.exr')
        choices = {dependency_key(plan.assets[0]): str(replacement)}
        self.project('other/missing.exr')
        updated = scan(self.script, replacements=choices)
        self.assertFalse(updated.assets[0].replacement)
        self.assertTrue(any(getattr(warning, 'key', '') == 'relink.stale' for warning in updated.warnings))

    def test_dropped_files_and_folders_match_together_without_duplicate_candidates(self):
        from wrapitupiso.engine.relink import find_dropped_candidates
        plan = self.project('lost/Plate.%04d.exr', 'Read {\n file lost/logo.png\n name Logo\n}\n')
        frame = self.file('deep/plate.1001.exr')
        self.file('deep/plate.1002.exr')
        logo = self.file('logo.png')
        matches, errors, preferred = find_dropped_candidates(plan.assets, [self.search, frame, logo, self.search / 'deep'])
        self.assertFalse(errors)
        sequence_key, logo_key = map(dependency_key, plan.assets)
        self.assertEqual(matches[sequence_key], [str(frame.with_name('plate.%04d.exr'))])
        self.assertEqual(matches[logo_key], [str(logo)])
        self.assertEqual(preferred, {key: paths[0] for key, paths in matches.items()})

    def test_renamed_dropped_files_are_manual_candidates_not_positional_matches(self):
        from wrapitupiso.engine.relink import find_dropped_candidates
        plan = self.project(extra='Read {\n file lost/other.exr\n name Other\n}\n')
        first, second = self.file('new_a.exr'), self.file('new_b.exr')
        matches, errors, preferred = find_dropped_candidates(plan.assets, [second, first])
        self.assertFalse(errors)
        self.assertFalse(preferred)
        for paths in matches.values(): self.assertEqual(paths, [str(first), str(second)])

    def test_single_dropped_frame_can_replace_multiple_selected_sequences(self):
        from wrapitupiso.engine.relink import find_dropped_candidates
        plan = self.project('lost/plate.####.exr', 'Read {\n file other.%04d.exr\n name Other\n}\n')
        frame = self.file('new.1001.exr')
        matches, _, preferred = find_dropped_candidates(plan.assets, [frame])
        self.assertEqual(len(preferred), 2)
        self.assertEqual([Path(preferred[dependency_key(a)]).name for a in plan.assets], ['new.####.exr', 'new.%04d.exr'])

    def test_dropped_candidates_respect_font_and_plugin_types(self):
        from wrapitupiso.engine.relink import find_dropped_candidates
        plan = self.project(extra='Text2 {\n font {invalid}\n name Title\n}\nTool {\n}\n')
        font, gizmo, media = self.font_file(), self.file('new.gizmo'), self.file('new.exr')
        matches, _, preferred = find_dropped_candidates([*plan.assets, *plan.plugins], [font, gizmo, media, self.script])
        self.assertFalse(preferred)
        for item in [*plan.assets, *plan.plugins]:
            expected = gizmo if item in plan.plugins else font if item.kind == 'font' else media
            self.assertEqual(matches[dependency_key(item)], [str(expected)])

    def test_dropped_missing_input_reports_error_and_search_can_cancel(self):
        from wrapitupiso.engine.relink import find_dropped_candidates
        plan = self.project()
        matches, errors, preferred = find_dropped_candidates(plan.assets, [self.root / 'gone'])
        self.assertTrue(errors)
        self.assertFalse(preferred)
        self.assertEqual(matches[dependency_key(plan.assets[0])], [])
        with self.assertRaises(Cancelled):
            find_dropped_candidates(plan.assets, [self.search], cancel=lambda: True)
