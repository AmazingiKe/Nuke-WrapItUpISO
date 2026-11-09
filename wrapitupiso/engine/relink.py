"""Explicit path replacements and cancellable folder lookup; never edit sources."""
import os
from pathlib import Path
import re
import stat

from wrapitupiso.config.i18n import message as msg

PLUGIN_EXTENSIONS = {'.gizmo', '.dll', '.so', '.dylib', '.ofx'}
MOVIE_EXTENSIONS = {'.mov', '.mp4', '.mxf', '.avi', '.mkv', '.webm'}


def dependency_key(dependency):
    from wrapitupiso.engine.gizmo import Plugin
    if isinstance(dependency, Plugin): return ('plugin', dependency.name)
    # Offsets plus the original value prevent applying a saved choice to another
    # knob after a source edit; owners distinguish project and nested Gizmos.
    return ('media', os.path.normcase(dependency.owner), dependency.start, dependency.end, dependency.raw)


def frame_token(token, field):
    """Keep the original notation when it matches; adapt to new padding otherwise."""
    if token == '<UDIM>': return token if len(field) == 4 and field.isdigit() else None
    fmt = token if token.startswith('%') else f'%0{len(token)}d'
    if fmt % int(field) == field: return token
    if field.startswith(' '): return f'%{len(field)}d'
    return '#' * len(field) if token.startswith('#') else f'%0{len(field)}d'


def manual_path(dependency, selected):
    from wrapitupiso.engine.core import SEQUENCE
    from wrapitupiso.engine.font_scan import FONT_EXTENSIONS
    from wrapitupiso.engine.gizmo import Plugin
    path = Path(selected).expanduser().resolve()
    if isinstance(dependency, Plugin):
        if path.suffix.lower() not in PLUGIN_EXTENSIONS: raise ValueError(msg('relink.plugin_type'))
    elif dependency.kind == 'font':
        if path.suffix.lower() not in FONT_EXTENSIONS: raise ValueError(msg('relink.font_type'))
    else:
        match = SEQUENCE.search(dependency.raw.replace('\\', '/').rsplit('/', 1)[-1])
        if match and not SEQUENCE.search(path.name) and path.suffix.lower() not in MOVIE_EXTENSIONS:
            # A selected frame represents the sequence. Prefer its last numeric
            # field so version numbers in the prefix stay literal.
            fields = list(re.finditer(r'-?\d+', path.stem))
            if not fields: raise ValueError(msg('relink.sequence_frame'))
            field = fields[-1]
            start = field.start()
            if field.group().startswith('-') and start and path.name[start - 1].isalnum(): start += 1
            token = frame_token(match.group(), path.name[start:field.end()])
            if token is None: raise ValueError(msg('relink.sequence_frame'))
            name = path.name[:start] + token + path.name[field.end():]
            path = path.with_name(name)
    return str(path)


def find_candidates(dependencies, folder, cancel=lambda: False, progress=lambda a,b,c: None):
    from wrapitupiso.engine.core import Cancelled, SEQUENCE
    from wrapitupiso.engine.gizmo import Plugin
    folder = Path(folder).expanduser().resolve()
    if cancel(): raise Cancelled()
    if not folder.is_dir(): raise FileNotFoundError(str(folder))
    files, errors = {}, []
    def failed(error): errors.append(str(error))
    count = 0
    for directory, dirs, names in os.walk(folder, onerror=failed, followlinks=False):
        if cancel(): raise Cancelled()
        descend = []
        for name in sorted(dirs):
            if cancel(): raise Cancelled()
            try:
                metadata = (Path(directory) / name).lstat()
                junction = getattr(metadata, 'st_reparse_tag', 0) == getattr(stat, 'IO_REPARSE_TAG_MOUNT_POINT', 0xA0000003)
                if not stat.S_ISLNK(metadata.st_mode) and not junction: descend.append(name)
            except OSError as error:
                failed(error)
        dirs[:] = descend
        for name in names:
            if cancel(): raise Cancelled()
            files.setdefault(name.casefold(), []).append(Path(directory) / name)
            count += 1
        progress(count, 0, str(directory))
    matches, sequence_cache = {}, {}
    for dependency in dependencies:
        if cancel(): raise Cancelled()
        key = dependency_key(dependency)
        name = (dependency.name if isinstance(dependency, Plugin) else dependency.raw).replace('\\', '/').rsplit('/', 1)[-1]
        candidates = []
        if isinstance(dependency, Plugin):
            names = [name] if Path(name).suffix.lower() in PLUGIN_EXTENSIONS else [name + ext for ext in sorted(PLUGIN_EXTENSIONS)]
            candidates = [path for filename in names for path in files.get(filename.casefold(), [])]
        elif SEQUENCE.search(name):
            if name not in sequence_cache:
                token = SEQUENCE.search(name)
                if len(list(SEQUENCE.finditer(name))) != 1:
                    sequence_cache[name] = []
                else:
                    pattern = re.compile(re.escape(name[:token.start()]) + r'( *-?\d+)' + re.escape(name[token.end():]), re.IGNORECASE)
                    found = set()
                    for paths in files.values():
                        if cancel(): raise Cancelled()
                        matched = pattern.fullmatch(paths[0].name)
                        if not matched: continue
                        number = matched.group(1)
                        value = frame_token(token.group(), number)
                        if value is None: continue
                        for path in paths:
                            found.add(path.with_name(path.name[:matched.start(1)] + value + path.name[matched.end(1):]))
                    sequence_cache[name] = sorted(found, key=lambda path: str(path).casefold())
            candidates = sequence_cache[name]
        else:
            candidates = files.get(name.casefold(), [])
        matches[key] = sorted({str(path) for path in candidates}, key=str.casefold)
    return matches, errors
