"""Windows Common Item Dialog: select .nk files and folders in one native view.

COM slots/signatures follow the Windows SDK IFileOpenDialog,
IFileDialogCustomize and IFileDialog(Control)Events declarations.
"""
import ctypes as C
from pathlib import Path
from uuid import UUID

P = C.c_void_p
U = C.c_uint32
H = C.c_int32
W = C.c_wchar_p
PP = C.POINTER(P)
GUID = C.c_ubyte * 16


def guid(value):
    return GUID.from_buffer_copy(UUID(value).bytes_le)


UNKNOWN = guid('00000000-0000-0000-c000-000000000046')
EVENTS = guid('973510db-7d7f-452b-8975-74a85828d354')
CONTROLS = guid('36116642-d713-4b97-9b83-7484a9d00433')
CUSTOMIZE = guid('e6fdd21a-163f-4975-9c8c-a69f1ba37034')
SHELL_ITEM = guid('43826d1e-e718-42ee-bc55-a1e261c37bfe')
ADD_SELECTION = 1001
CANCELLED = 0x800704C7


def check(result):
    if result < 0:
        raise OSError(f'Windows 文件选择器错误：0x{result & 0xffffffff:08X}')
    return result


def invoke(pointer, slot, types=(), *args):
    table = C.cast(pointer, C.POINTER(PP)).contents
    method = C.WINFUNCTYPE(H, P, *types)(table[slot])
    return method(pointer, *args)


def release(pointer):
    if pointer.value:
        invoke(pointer, 2)
        pointer.value = None


def valid_projects(paths):
    selected = [Path(path) for path in paths]
    for path in selected:
        if not (path.is_dir() or (path.is_file() and path.suffix.lower() == '.nk')):
            raise ValueError(f'请选择有效的文件夹或 .nk 工程：\n{path}')
    return selected


def show_native_error(owner, error):
    user = C.WinDLL('user32')
    user.MessageBoxW.argtypes = [P, W, W, U]
    user.MessageBoxW(owner, str(error), '无法添加所选项目', 0x10)


class DialogEvents:
    """Own both COM vtables and callbacks until after Unadvise/Release."""
    def __init__(self, owner):
        self.owner = owner
        self.refs = 1
        self.callbacks = []
        common = [(self.query, H, [P, PP]), (self.add_ref, U, []),
                  (self.release_ref, U, [])]
        self.events = self.interface(common + [
            (self.ok, H, [P]), (self.ok, H, [P, P]),
            (self.ok, H, [P]), (self.ok, H, [P]),
            (self.response, H, [P, P, C.POINTER(U)]),
            (self.ok, H, [P]), (self.response, H, [P, P, C.POINTER(U)]),
        ])
        self.controls = self.interface(common + [
            (self.ok, H, [P, U, U]), (self.on_button, H, [P, U]),
            (self.ok, H, [P, U, H]), (self.ok, H, [P, U]),
        ])

    def interface(self, methods):
        callbacks = [C.WINFUNCTYPE(restype, P, *types)(fn)
                     for fn, restype, types in methods]
        self.callbacks.extend(callbacks)
        table = (P * len(callbacks))(*(C.cast(fn, P).value for fn in callbacks))
        # Keep the table and the one-pointer interface object alive together.
        return table, P(C.addressof(table))

    def query(self, this, iid, output):
        requested = C.string_at(iid, 16)
        if requested in (bytes(UNKNOWN), bytes(EVENTS)):
            output[0] = C.addressof(self.events[1])
        elif requested == bytes(CONTROLS):
            output[0] = C.addressof(self.controls[1])
        else:
            output[0] = None
            return -2147467262  # E_NOINTERFACE
        self.refs += 1
        return 0

    def add_ref(self, this):
        self.refs += 1
        return self.refs

    def release_ref(self, this):
        self.refs -= 1
        return self.refs

    def ok(self, *args):
        return 0

    def response(self, this, dialog, item, response):
        response[0] = 0  # Let Windows apply its default policy.
        return 0

    def on_button(self, this, customize, control):
        if control != ADD_SELECTION:
            return 0
        try:
            selected = valid_projects(self.owner.selected_items())
            if selected:
                self.owner.result = selected
                self.owner.close(0)
        except Exception as error:
            # Exceptions must never escape a ctypes callback into COM.
            show_native_error(self.owner.hwnd, error)
        return 0


class FilterSpec(C.Structure):
    _fields_ = [('name', W), ('pattern', W)]


class NativeProjectDialog:
    def __init__(self, initialdir):
        self.initialdir = Path(initialdir)
        self.dialog = P()
        self.customize = P()
        self.cookie = U()
        self.advised = False
        self.initialized = False
        self.result = None
        self.hwnd = 0
        # WinDLL returns HRESULTs rather than automatically raising on errors.
        self.ole = C.WinDLL('ole32')
        self.ole.CoInitializeEx.argtypes = [P, U]
        self.ole.CoInitializeEx.restype = H
        self.ole.CoCreateInstance.argtypes = [P, P, U, P, PP]
        self.ole.CoCreateInstance.restype = H
        self.ole.CoTaskMemFree.argtypes = [P]
        self.ole.CoTaskMemFree.restype = None
        self.ole.CoUninitialize.argtypes = []
        self.ole.CoUninitialize.restype = None

    def call(self, slot, types=(), *args):
        return invoke(self.dialog, slot, types, *args)

    def __enter__(self):
        try:
            check(self.ole.CoInitializeEx(None, 2))  # apartment-threaded
            self.initialized = True
            clsid = guid('dc1c5a9c-e88a-4dde-a5a1-60f82a20aef7')
            iid = guid('d57c7288-d4ad-4768-be02-9d969532d960')
            check(self.ole.CoCreateInstance(C.byref(clsid), None, 1, C.byref(iid), C.byref(self.dialog)))
            options = U()
            check(self.call(10, [C.POINTER(U)], C.byref(options)))
            # filesystem + multi-select + existing paths/files + unchanged cwd
            check(self.call(9, [U], options.value | 0x40 | 0x200 | 0x800 | 0x1000 | 0x8))
            filters = (FilterSpec * 1)(FilterSpec('Nuke 工程 (*.nk)', '*.nk'))
            check(self.call(4, [U, C.POINTER(FilterSpec)], 1, filters))
            check(self.call(17, [W], '添加工程：选择文件夹或 .nk'))
            check(self.call(18, [W], '打开'))
            self.set_initial_directory()
            check(self.call(0, [P, PP], C.byref(CUSTOMIZE), C.byref(self.customize)))
            check(invoke(self.customize, 11, [U, W], 1002,
                         'Ctrl / Shift 多选文件夹或 .nk，然后点击“添加选中项”。'))
            check(invoke(self.customize, 5, [U, W], ADD_SELECTION, '添加选中项'))
            check(invoke(self.customize, 28, [U], ADD_SELECTION))
            self.events = DialogEvents(self)
            check(self.call(7, [P, C.POINTER(U)], C.byref(self.events.events[1]), C.byref(self.cookie)))
            self.advised = True
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def set_initial_directory(self):
        if not self.initialdir.is_dir():
            return
        shell = C.WinDLL('shell32')
        shell.SHCreateItemFromParsingName.argtypes = [W, P, P, PP]
        shell.SHCreateItemFromParsingName.restype = H
        item = P()
        try:
            check(shell.SHCreateItemFromParsingName(str(self.initialdir), None,
                                                    C.byref(SHELL_ITEM), C.byref(item)))
            check(self.call(12, [P], item))
        finally:
            release(item)

    def selected_items(self, slot=28):
        items = P()
        try:
            check(self.call(slot, [PP], C.byref(items)))
            count = U()
            check(invoke(items, 7, [C.POINTER(U)], C.byref(count)))
            paths = []
            for index in range(count.value):
                item, name = P(), P()
                try:
                    check(invoke(items, 8, [U, PP], index, C.byref(item)))
                    check(invoke(item, 5, [U, PP], 0x80058000, C.byref(name)))
                    paths.append(Path(C.wstring_at(name)))
                finally:
                    if name.value:
                        self.ole.CoTaskMemFree(name)
                    release(item)
            return paths
        finally:
            release(items)

    def close(self, result):
        check(self.call(23, [H], result))

    def show(self, hwnd):
        self.hwnd = hwnd
        result = self.call(3, [P], hwnd)
        if result & 0xffffffff == CANCELLED:
            return []
        check(result)
        return self.result if self.result is not None else valid_projects(self.selected_items(27))

    def __exit__(self, *args):
        if self.advised:
            self.call(8, [U], self.cookie.value)
            self.advised = False
        release(self.customize)
        release(self.dialog)
        if self.initialized:
            self.ole.CoUninitialize()
            self.initialized = False


def ask_projects(parent, initialdir):
    user = C.WinDLL('user32')
    user.GetAncestor.argtypes = [P, U]
    user.GetAncestor.restype = P
    hwnd = user.GetAncestor(int(parent.winId()), 2)
    with NativeProjectDialog(initialdir) as dialog:
        return dialog.show(hwnd)
