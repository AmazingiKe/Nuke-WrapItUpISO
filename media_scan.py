"""Directory and sequence results shared only within one dependency scan."""
import os
from pathlib import Path
import re


class MediaIndex:
    def __init__(self, cancel, snapshot=None):
        self.cancel, self.snapshot = cancel, snapshot
        self.directories, self.file_flags, self.patterns = {}, {}, {}

    def check_cancel(self):
        from core import Cancelled
        if self.cancel(): raise Cancelled()

    def entries(self, folder):
        self.check_cancel()
        if folder not in self.directories:
            if self.snapshot: self.snapshot.watch(folder)
            entries = []
            try:
                with os.scandir(folder) as iterator:
                    for entry in iterator:
                        self.check_cancel()
                        entries.append(Path(entry.path))
            except FileNotFoundError:
                pass
            except OSError:
                if self.snapshot: self.snapshot.reusable = False
                raise
            self.check_cancel()
            self.directories[folder] = sorted(entries)
        return self.directories[folder]

    def is_file(self, path):
        self.check_cancel()
        if path not in self.file_flags:
            if self.snapshot: self.snapshot.watch(path)
            try:
                self.file_flags[path] = path.is_file()
            except OSError:
                if self.snapshot: self.snapshot.reusable = False
                raise
        return self.file_flags[path]

    def matches(self, source, match):
        self.check_cancel()
        if source not in self.patterns:
            token = match.group()
            width = int(match.group(1)) if token.startswith('%') and match.group(1) else None
            if token.startswith('#'): width = len(token)
            fmt = token if token.startswith('%') else '%0' + str(width) + 'd'
            pattern = re.compile(re.escape(source.name[:match.start()]) + r'( *-?\d+)'
                                 + re.escape(source.name[match.end():]))
            files, numbers = [], []
            for path in self.entries(source.parent):
                self.check_cancel()
                found = pattern.fullmatch(path.name)
                if not found: continue
                field = found.group(1)
                if token == '<UDIM>':
                    if len(field) != 4 or not field.isdigit(): continue
                    number = int(field)
                else:
                    # Padding is a minimum width including a minus sign, not a digit count.
                    if width and len(field) < width: continue
                    number = int(field)
                    if fmt % number != field: continue
                if self.is_file(path):
                    files.append(path); numbers.append(number)
            self.patterns[source] = (tuple(files), tuple(numbers))
        return self.patterns[source]
