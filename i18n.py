"""JSON translations and deferred messages, independent of Qt and worker state.

Message is a Chinese-compatible str for existing reports and integrations.
Its key and parameters remain available for rendering in another language.
File paths and other user data are never interpreted as translation keys.
"""
import json
from pathlib import Path
from string import Formatter

LANGUAGES = {'zh-CN': '简体中文', 'en': 'English', 'ja': '日本語'}
CATALOG = json.loads((Path(__file__).parent / 'locales/translations.json').read_text(encoding='utf-8'))


def normalize_language(value):
    prefix = value.replace('_', '-').lower().split('-')[0] if isinstance(value, str) else ''
    return {'zh': 'zh-CN', 'ja': 'ja'}.get(prefix, 'en')


def validate_catalog(catalog):
    formatter = Formatter()
    for key, variants in catalog.items():
        if not isinstance(variants, dict) or set(variants) != set(LANGUAGES):
            raise ValueError(f'{key}: missing language')
        signatures = []
        for language, text in variants.items():
            if not isinstance(text, str) or not text: raise ValueError(f'{key}: empty translation for {language}')
            signatures.append({(field, spec, conversion) for _, field, spec, conversion in formatter.parse(text) if field is not None})
        if any(signature != signatures[0] for signature in signatures[1:]):
            raise ValueError(f'{key}: translation placeholders differ')


class Message(str):
    def __new__(cls, key, params=None, template=None):
        params = params or {}
        source = template if template is not None else CATALOG[key]['zh-CN']
        value = source.format(**{k: Translator('zh-CN').render(v) for k, v in params.items()})
        instance = super().__new__(cls, value)
        instance.key, instance.params, instance.template = key, params, template
        return instance

    def __add__(self, other):
        return fmt('{left}{right}', left=self, right=other)

    def __radd__(self, other):
        return fmt('{left}{right}', left=other, right=self)

    def __reduce__(self):
        return Message, (self.key, self.params, self.template)


def message(key, **params):
    return Message(key, params)


def fmt(template, **params):
    if not any(isinstance(value, (Message, BaseException)) for value in params.values()):
        return template.format(**params)
    return Message(None, params, template)


def join_messages(separator, values):
    values = list(values)
    params = {f'part{i}': value for i, value in enumerate(values)}
    template = '{separator}'.join('{' + key + '}' for key in params)
    return fmt(template, separator=separator, **params)


def error_message(error):
    if isinstance(error, Message): return error
    if isinstance(error, BaseException) and len(error.args) == 1 and isinstance(error.args[0], Message):
        return error.args[0]
    return str(error)


# Old reports use Chinese strings. New in-memory messages carry stable keys.
_LEGACY_STATUSES = {variants['zh-CN']: key[7:] for key, variants in CATALOG.items()
                    if key.startswith('status.') and not key.endswith('_fragment')}


def status_code(value):
    if isinstance(value, Message) and value.key and value.key.startswith('status.'):
        return value.key[7:]
    return _LEGACY_STATUSES.get(value, value)


def status_message(value):
    key = 'status.' + status_code(value)
    return message(key) if key in CATALOG else value


class Translator:
    def __init__(self, language='zh-CN'):
        self.language = normalize_language(language)

    def render(self, value):
        if isinstance(value, (list, tuple)): return [self.render(part) for part in value]
        if isinstance(value, Message):
            source = value.template if value.template is not None else CATALOG[value.key].get(self.language, CATALOG[value.key]['en'])
            return source.format(**{k: self.render(v) for k, v in value.params.items()})
        if isinstance(value, BaseException): return self.render(error_message(value))
        return value

    def __call__(self, key, **params):
        return self.render(message(key, **params))
