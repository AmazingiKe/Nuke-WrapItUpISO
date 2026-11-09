"""Shared version metadata for the source app and PyInstaller bundle."""
import json
from wrapitupiso.config.paths import bundle_root

METADATA = json.loads(
    (bundle_root() / 'version.json').read_text(encoding='utf-8')
)
APP_NAME = METADATA['name']
VERSION = METADATA['version']
APP_LABEL = f'{APP_NAME} {VERSION}'
APP_TAGLINE = METADATA['tagline']
