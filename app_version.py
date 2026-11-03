"""Shared version metadata for the source app and PyInstaller bundle."""
import json
from pathlib import Path

VERSION = json.loads(
    Path(__file__).with_name('version.json').read_text(encoding='utf-8')
)['version']
