"""Small, portable JSON preferences stored beside the application."""
from datetime import datetime
import json
import os
from pathlib import Path
from queue import Queue
import tempfile
import threading

from wrapitupiso.config.i18n import normalize_language
from wrapitupiso.config.paths import application_root


def default_settings_path():
    return application_root() / 'wrapitupiso_settings.json'


def defaults(language):
    return {'schema_version': 1, 'language': normalize_language(language),
            'layout': {'window': {'width': 1240, 'height': 820, 'maximized': False},
                       'panes': [340, 880], 'footer_panes': [600, 600], 'vertical_panes': [460, 190],
                       'project_columns': [190, 105, 50], 'asset_columns': [140, 58, 350, 62, 54]},
            'only_issues': False, 'output_directory': '', 'last_project_directory': ''}


def positive_sizes(value, count):
    return isinstance(value, list) and len(value) == count and all(type(v) is int and 0 < v <= 32768 for v in value)


def validated(data, language):
    result = defaults(language)
    if isinstance(data.get('language'), str):
        result['language'] = normalize_language(data['language'])
    for key in ('output_directory', 'last_project_directory'):
        if isinstance(data.get(key), str): result[key] = data[key]
    if type(data.get('only_issues')) is bool: result['only_issues'] = data['only_issues']
    layout = data.get('layout')
    if isinstance(layout, dict):
        for key, count in (('panes', 2), ('footer_panes', 2), ('vertical_panes', 2), ('project_columns', 3), ('asset_columns', 5)):
            if positive_sizes(layout.get(key), count): result['layout'][key] = layout[key].copy()
        window = layout.get('window')
        if isinstance(window, dict):
            for key, minimum in (('width', 900), ('height', 620)):
                value = window.get(key)
                if type(value) is int and minimum <= value <= 32768: result['layout']['window'][key] = value
            if type(window.get('maximized')) is bool: result['layout']['window']['maximized'] = window['maximized']
            if all(type(window.get(key)) is int and abs(window[key]) <= 100000 for key in ('x', 'y')):
                result['layout']['window'].update(x=window['x'], y=window['y'])
    return result


class SettingsStore:
    def __init__(self, path=None, *, system_language='en'):
        self.path = Path(path) if path is not None else default_settings_path()
        self.system_language = system_language
        self.data = defaults(system_language)
        self.error = None
        self.recovered = False
        self.read_only = False
        try:
            source = self.path
            if path is None and not source.exists():
                legacy = source.parent / 'settings' / source.name
                if legacy.is_file():
                    source = legacy
            if source.exists():
                try:
                    content = json.loads(source.read_text(encoding='utf-8-sig'))
                    if not isinstance(content, dict): raise ValueError('Settings must be an object')
                except (ValueError, UnicodeError):
                    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
                    source.rename(source.with_name(f'wrapitupiso_settings.invalid-{stamp}.json'))
                    self.recovered = True
                else:
                    version = content.get('schema_version', 1)
                    if type(version) is not int or version != 1:
                        self.read_only = True
                        raise ValueError('Unsupported settings schema version')
                    self.data = validated(content, system_language)
            self.save()
        except (OSError, ValueError) as error:
            self.error = error
            self.read_only = True

    def save(self):
        if self.read_only: return False
        self.data = validated(self.data, self.system_language)
        self.error = write_settings(self.path, self.data)
        return self.error is None


def write_settings(path, data):
    """Atomically write an independent snapshot; return an error without touching UI state."""
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent,
                                         prefix='.wrapitupiso-', suffix='.tmp', delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError as error:
        return error
    finally:
        if temporary is not None:
            try: temporary.unlink(missing_ok=True)
            except OSError: pass
    return None


class SettingsWriter:
    """One serial writer, with at most one pending snapshot and a scan pause gate.

    Only plain data crosses threads. pause() prevents new writes immediately;
    a scan worker can then wait_idle() for an existing write without blocking Qt.
    close() drains the final snapshot before the application exits.
    """
    def __init__(self, path, system_language):
        self.path = Path(path)
        self.system_language = system_language
        self.results = Queue()
        self.condition = threading.Condition()
        self.pending = None
        self.active = self.paused = self.closed = False
        self.thread = threading.Thread(target=self._run, name='settings-writer', daemon=False)
        self.thread.start()

    def submit(self, data):
        snapshot = validated(data, self.system_language)
        with self.condition:
            if self.closed: return
            self.pending = snapshot
            self.condition.notify_all()

    def pause(self):
        with self.condition:
            self.paused = True

    def resume(self):
        with self.condition:
            self.paused = False
            self.condition.notify_all()

    def wait_idle(self):
        with self.condition:
            self.condition.wait_for(lambda: not self.active)

    def close(self):
        with self.condition:
            self.closed = True
            self.condition.notify_all()
        self.thread.join()

    def _run(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.closed or (self.pending is not None and not self.paused))
                if self.pending is None: return
                snapshot, self.pending = self.pending, None
                self.active = True
            error = write_settings(self.path, snapshot)
            self.results.put(error)
            with self.condition:
                self.active = False
                self.condition.notify_all()
