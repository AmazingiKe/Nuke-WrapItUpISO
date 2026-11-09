"""Small, portable JSON preferences stored beside the application."""
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import tempfile

from i18n import normalize_language


def default_settings_path():
    root = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
    return root / 'settings' / 'wrapitupiso_settings.json'


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
            if self.path.exists():
                try:
                    content = json.loads(self.path.read_text(encoding='utf-8-sig'))
                    if not isinstance(content, dict): raise ValueError('Settings must be an object')
                except (ValueError, UnicodeError):
                    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
                    self.path.rename(self.path.with_name(f'wrapitupiso_settings.invalid-{stamp}.json'))
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
        temporary = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.data = validated(self.data, self.system_language)
            with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=self.path.parent,
                                             prefix='.wrapitupiso-', suffix='.tmp', delete=False) as handle:
                temporary = Path(handle.name)
                json.dump(self.data, handle, ensure_ascii=False, indent=2)
                handle.write('\n')
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            self.error = None
            return True
        except OSError as error:
            self.error = error
            return False
        finally:
            if temporary is not None:
                try: temporary.unlink(missing_ok=True)
                except OSError: pass
