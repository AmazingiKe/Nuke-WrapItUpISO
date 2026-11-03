import ctypes as C
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from project_picker import (NativeProjectDialog, DialogEvents, valid_projects,
                            invoke, release, guid, EVENTS, CONTROLS, UNKNOWN, P, PP, H, U)

class ProjectPickerTests(unittest.TestCase):
    def test_mixed_paths_and_case_insensitive_nuke_extension(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root) / '文件夹'; folder.mkdir()
            nk = Path(root) / '镜头.NK'; nk.touch()
            self.assertEqual(valid_projects([folder, nk]), [folder, nk])
            with self.assertRaises(ValueError):
                valid_projects([folder, Path(root) / 'missing.nk'])
            txt = Path(root) / 'ignore.txt'; txt.touch()
            with self.assertRaises(ValueError):
                valid_projects([txt])

    def test_native_com_configuration_and_cleanup(self):
        with NativeProjectDialog(Path.home()) as dialog:
            self.assertTrue(dialog.dialog.value)
            options = C.c_uint32()
            dialog.call(10, [C.POINTER(C.c_uint32)], C.byref(options))
            self.assertTrue(options.value & 0x200)
            self.assertFalse(options.value & 0x20)
            self.assertGreater(dialog.events.refs, 1)
        self.assertFalse(dialog.dialog.value)
        self.assertEqual(dialog.events.refs, 1)

    def test_button_accepts_selection_and_closes(self):
        owner = Mock()
        with tempfile.TemporaryDirectory() as root:
            owner.selected_items.return_value = [Path(root)]
            events = DialogEvents(owner)
            events.on_button(None, None, 1001)
            self.assertEqual(owner.result, [Path(root)])
            owner.close.assert_called_once_with(0)

    def test_empty_selection_keeps_dialog_open(self):
        owner = Mock(); owner.selected_items.return_value = []
        events = DialogEvents(owner)
        events.on_button(None, None, 1001)
        owner.close.assert_not_called()

    def test_callback_failure_never_crosses_com_boundary(self):
        owner = Mock(); owner.selected_items.side_effect = OSError('无法访问')
        events = DialogEvents(owner)
        with patch('project_picker.show_native_error') as error:
            self.assertEqual(events.on_button(None, None, 1001), 0)
        error.assert_called_once()
        owner.close.assert_not_called()

    def test_cancel_returns_empty_and_unexpected_error_raises(self):
        with NativeProjectDialog(Path.home()) as dialog:
            with patch.object(dialog, 'call', return_value=-2147023673):
                self.assertEqual(dialog.show(0), [])
            with patch.object(dialog, 'call', return_value=-2147467259):
                with self.assertRaises(OSError):
                    dialog.show(0)

    def test_native_shell_array_preserves_multiple_unicode_paths(self):
        # Real Shell objects cover Unicode, mixed folders/files and COM memory ownership.
        with tempfile.TemporaryDirectory() as root, NativeProjectDialog(root) as dialog:
            folder = Path(root) / '目录'; folder.mkdir()
            nk = Path(root) / '镜头.NK'; nk.touch()
            shell = C.WinDLL('shell32')
            shell.SHParseDisplayName.argtypes = [C.c_wchar_p, P, PP, U, P]
            shell.SHParseDisplayName.restype = H
            shell.SHCreateShellItemArrayFromIDLists.argtypes = [U, PP, PP]
            shell.SHCreateShellItemArrayFromIDLists.restype = H
            pidls = (P * 2)()
            array = P()
            try:
                for index, path in enumerate([folder, nk]):
                    item = P()
                    self.assertEqual(shell.SHParseDisplayName(str(path), None, C.byref(item), 0, None), 0)
                    pidls[index] = item.value
                self.assertEqual(shell.SHCreateShellItemArrayFromIDLists(2, pidls, C.byref(array)), 0)
                def result_array(slot, types, output):
                    self.assertEqual(slot, 28)
                    invoke(array, 1)  # transferred reference owned by selected_items
                    C.cast(output, PP)[0] = array.value
                    return 0
                with patch.object(dialog, 'call', side_effect=result_array):
                    self.assertEqual(dialog.selected_items(), [folder, nk])
            finally:
                release(array)
                for pidl in pidls:
                    dialog.ole.CoTaskMemFree(pidl)

    def test_event_interfaces_query_and_reference_counts(self):
        events = DialogEvents(Mock())
        pointer = C.byref(events.events[1])
        for iid in (UNKNOWN, EVENTS, CONTROLS):
            out = P()
            self.assertEqual(invoke(pointer, 0, [P, PP], C.byref(iid), C.byref(out)), 0)
            self.assertEqual(events.refs, 2)
            release(out)
            self.assertEqual(events.refs, 1)
        out = P()
        self.assertLess(invoke(pointer, 0, [P, PP], C.byref(guid('11111111-1111-1111-1111-111111111111')), C.byref(out)), 0)
        self.assertFalse(out.value)
        response = U(99)
        self.assertEqual(invoke(pointer, 7, [P, P, C.POINTER(U)], None, None, C.byref(response)), 0)
        self.assertEqual(response.value, 0)
        self.assertEqual(invoke(pointer, 3, [P], None), 0)
        events.on_button(None, None, 999)
        events.owner.selected_items.assert_not_called()

    def test_configuration_error_releases_dialog_and_com(self):
        dialog = NativeProjectDialog(Path.home())
        with patch.object(dialog, 'call', return_value=-2147467259):
            with self.assertRaises(OSError):
                dialog.__enter__()
        self.assertFalse(dialog.dialog.value)
        self.assertFalse(dialog.initialized)

    def test_normal_open_results_and_custom_button_results(self):
        with tempfile.TemporaryDirectory() as root, NativeProjectDialog(Path(root) / 'missing') as dialog:
            with patch.object(dialog, 'call', return_value=0), patch.object(dialog, 'selected_items', return_value=[Path(root)]) as selected:
                self.assertEqual(dialog.show(0), [Path(root)])
                selected.assert_called_once_with(27)
                selected.reset_mock()
                dialog.result = [Path(root)]
                self.assertEqual(dialog.show(0), [Path(root)])
                selected.assert_not_called()
                dialog.close(0)

if __name__ == '__main__':
    unittest.main()
