"""Shared version metadata for the source app and PyInstaller bundle."""
import json
from pathlib import Path

METADATA = json.loads(
    Path(__file__).with_name('version.json').read_text(encoding='utf-8')
)
APP_NAME = METADATA['name']
VERSION = METADATA['version']
APP_LABEL = f'{APP_NAME} {VERSION}'
