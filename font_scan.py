"""Collect font files without importing Nuke or evaluating project expressions."""
from i18n import message as msg, fmt, join_messages, error_message
import os
from pathlib import Path
import re
import sys


FONT_EXTENSIONS = {'.ttf', '.otf', '.ttc', '.otc', '.pfa', '.pfb'}
FONT_RECORD = re.compile(r'([^{}:]+?)\s*:\s*([^{}:]+?)\s*:\s*(?P<file>[^{}]+?)\s*:\s*\d+(?=\s*[}]|\s*$)')


def font_files(source, media_index):
    files = [source] if media_index.is_file(source) else []
    if files and source.suffix.lower() in {'.pfa', '.pfb'}:
        for suffix in ('.afm', '.pfm'):
            companion = source.with_suffix(suffix)
            if media_index.is_file(companion):
                files.append(companion)
    return files


def search_folders(plan, cancel):
    from core import node_blocks, knobs, resolve
    folders, errors, system_fonts = [], [], True
    for cls, pos, body in node_blocks(plan.text):
        if cls != 'Root':
            continue
        values = knobs(body, pos)
        system_fonts = values.get('free_type_system_fonts')
        system_fonts = system_fonts is None or system_fonts.value not in {'false', '0'}
        control = values.get('free_type_font_path')
        if control:
            for raw in control.value.split(os.pathsep):
                if raw.strip():
                    try:
                        folders.append(resolve(raw.strip(), plan.base).resolve())
                    except ValueError as error:
                        errors.append(msg('font.search_path_error') + error_message(error))
    folders.append(Path.home() / '.nuke' / 'fonts')
    plugin_folders = plan.search_paths
    if not plugin_folders:
        from gizmo import default_paths
        from plugin_paths import discover_paths
        discovered = discover_paths(default_paths(), cancel, observe=plan.snapshot.watch if plan.snapshot else None)
        plugin_folders = discovered.paths
        errors.extend(discovered.errors)
    for folder in plugin_folders:
        folders.extend((Path(folder), Path(folder) / 'fonts'))
    for raw in os.environ.get('NUKE_FONT_PATH', '').split(os.pathsep):
        if raw.strip():
            try:
                folders.append(resolve(raw.strip('"'), plan.base).resolve())
            except ValueError as error:
                errors.append('NUKE_FONT_PATH：' + error_message(error))
    # A saved script identifies its Nuke version; no installed Nuke is required.
    header = re.search(r'^#!\s+(.+?)[/\\]nuke-[\d.]+\.(?:dll|so|dylib)', plan.text, re.M | re.I)
    if header:
        folders.append(Path(header[1]) / 'plugins' / 'fonts')
    version = re.search(r'^version\s+(\d+\.\d+)\s+v(\d+)', plan.text, re.M)
    if version and os.name == 'nt':
        folders.append(Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / fmt('Nuke{value0}v{value1}', value0=version[1], value1=version[2]) / 'plugins' / 'fonts')
    folders.append(plan.script.parent / 'fonts')
    if system_fonts:
        if os.name == 'nt':
            folders.append(Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts')
            if os.environ.get('LOCALAPPDATA'):
                folders.append(Path(os.environ['LOCALAPPDATA']) / 'Microsoft' / 'Windows' / 'Fonts')
        elif sys.platform == 'darwin':
            folders.extend((Path.home() / 'Library/Fonts', Path('/Library/Fonts'), Path('/System/Library/Fonts')))
        else:
            folders.extend((Path.home() / '.fonts', Path.home() / '.local/share/fonts', Path('/usr/share/fonts'), Path('/usr/local/share/fonts')))
    return list(dict.fromkeys(folders)), errors


def collect_fonts(plan, cancel, media_index):
    from core import Asset, Cancelled, Token, knobs, node_blocks, resolve
    documents = []
    for owner, (text, _, _) in plan.documents.items():
        nodes = []
        for block in node_blocks(text, scoped=True):
            if cancel():
                raise Cancelled()
            if block[0] == 'Text2':
                nodes.append(block)
        if nodes:
            documents.append((owner, text, nodes))
    if not documents:
        return
    folders, errors = search_folders(plan, cancel)
    for owner, text, nodes in documents:
        for cls, pos, body, groups in nodes:
            if cancel():
                raise Cancelled()
            values = knobs(body, pos)
            name = values.get('name', Token(cls, 0, 0)).value
            token = values.get('font')
            raw = text[token.start:token.end] if token else '{ Utopia : Regular : UtopiaRegular.pfa : 0 }'
            matches = list(FONT_RECORD.finditer(raw))
            if not matches:
                start, end = (token.start, token.end) if token else (pos + len(body),) * 2
                plan.assets.append(Asset(name, 'font', raw, start, end, status=msg('status.pending'), note=msg('font.parse_error'), owner=owner, node_class=cls, node_path='_'.join((*groups, name)), kind='font'))
            for match in matches:
                filename = match['file'].strip()
                start = token.start + match.start('file') if token else pos + len(body)
                end = token.start + match.end('file') if token else start
                asset = Asset(name, 'font', filename, start, end, owner=owner,
                              node_class=cls, node_path='_'.join((*groups, name)), kind='font',
                              font_mode='family' if token else 'default')
                plan.assets.append(asset)
                try:
                    # Family-record filenames use font search paths independently
                    # of a dynamic project media directory.
                    is_path = Path(filename).is_absolute() or '/' in filename or '\\' in filename
                    source = resolve(filename, plan.base if is_path else plan.script.parent).resolve()
                    candidates = [source] if is_path else [folder / filename for folder in folders]
                    for candidate in candidates:
                        if cancel():
                            raise Cancelled()
                        if plan.snapshot:
                            plan.snapshot.watch(candidate.parent)
                        files = font_files(candidate, media_index)
                        if files:
                            asset.source, asset.files = str(candidate), files
                            break
                    asset.status = msg('status.ready') if asset.files else (msg('status.pending') if errors else msg('status.missing'))
                    asset.note = join_messages(msg('common.note_separator'), errors) if errors else msg('font.search_note')
                    if plan.snapshot:
                        for file in asset.files:
                            plan.snapshot.watch(file)
                except (ValueError, OSError) as error:
                    asset.status, asset.note = msg('status.pending'), error_message(error)


def font_destinations(assets):
    """Flat fonts folder with lowercase names and distinct collision suffixes."""
    import hashlib
    fonts = {path for asset in assets if asset.kind == 'font' for path in asset.files}
    result, used, stems = {}, set(), {}
    for path in sorted(fonts):
        family = path.with_suffix('')
        if family not in stems:
            name = path.stem.lower()
            if name.casefold() in used:
                suffix = hashlib.sha256(str(family).encode()).hexdigest()[:10]
                name += '_' + suffix
            while name.casefold() in used:
                name = '_' + name
            used.add(name.casefold())
            stems[family] = name
        result[path] = 'fonts/' + stems[family] + path.suffix.lower()
    return result
