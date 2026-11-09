"""Keep bundled resources separate from portable user preferences."""
from pathlib import Path
import sys


def bundle_root():
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS).resolve()
    return Path(__file__).resolve().parents[2]


def resource_path(relative):
    return bundle_root() / 'wrapitupiso' / 'resources' / relative


def application_root():
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return bundle_root()
