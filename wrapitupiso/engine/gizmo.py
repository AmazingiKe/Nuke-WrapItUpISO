"""Static Gizmo dependency collection. Never import a source init/menu script."""
from wrapitupiso.config.i18n import status_code, status_message
from wrapitupiso.config.i18n import message as msg, fmt, join_messages, error_message
from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import re


@dataclass
class Plugin:
    name: str
    status: str = msg('status.not_found')
    source: str = ''
    candidates: list = field(default_factory=list)
    note: str = ''
    used_by: list = field(default_factory=list)
    dependencies: list = field(default_factory=list)
    sha256: str = ''
    packed_name: str = ''


def digest_file(path, cancel=lambda: False, progress=None):
    from wrapitupiso.engine.core import Cancelled
    digest = hashlib.sha256()
    done = 0
    with Path(path).open('rb') as stream:
        while True:
            if cancel():
                raise Cancelled()
            chunk = stream.read(4 * 1024 * 1024)
            if not chunk:
                return digest.hexdigest()
            digest.update(chunk)
            done += len(chunk)
            if progress: progress(done)


def default_paths():
    return [Path.home() / '.nuke'] + [Path(os.path.expandvars(p.strip('"'))) for p in os.environ.get('NUKE_PATH', '').split(os.pathsep) if p.strip()]


def index_plugins(paths, cancel, progress, *, observe=None):
    from wrapitupiso.engine.core import Cancelled
    index, errors, visited = {}, [], set()
    for root in paths:
        if cancel():
            raise Cancelled()
        root = Path(root).expanduser().resolve()
        if observe: observe(root)
        if not root.is_dir():
            # Ignore stale search paths; missing referenced plugins are reported below.
            continue
        if root in visited: continue
        visited.add(root)
        progress(0, 0, msg('phase.find_plugins', name=root.name))
        try:
            files = []
            # Nuke searches registered directories, not arbitrary backup subfolders.
            with os.scandir(root) as entries:
                for entry in entries:
                    if cancel(): raise Cancelled()
                    file = Path(entry.path)
                    if file.suffix.lower() in {'.gizmo', '.dll', '.so', '.dylib'} and entry.is_file():
                        files.append(file)
            for file in sorted(files):
                index.setdefault(file.stem.casefold(), []).append(file)
        except OSError as error:
            errors.append(msg('plugin.folder_error', path=error.filename or root))
    return index, errors


def file_knobs(text):
    """File controls and link controls forwarding a known file knob."""
    from wrapitupiso.engine.core import node_blocks, declared_file_knobs
    for cls, pos, body in node_blocks(text):
        # Only the outer definition declares knobs visible on Gizmo instances.
        return declared_file_knobs(body)
    return set()


def validate_definition(text):
    from wrapitupiso.engine.core import node_blocks, words
    blocks = list(node_blocks(text))
    if not blocks or blocks[0][0] not in {'Gizmo', 'Group'}:
        raise ValueError(msg('gizmo.no_definition'))
    tokens = list(words(text))
    depth = 0
    started = False
    for i, token in enumerate(tokens):
        line_start = text.rfind('\n', 0, token.start) + 1
        if text[line_start:token.start].strip():
            continue
        if token.value == 'end_group':
            depth -= 1
            if depth < 0:
                raise ValueError(msg('gizmo.extra_end'))
        elif i + 1 < len(tokens) and text[tokens[i + 1].start:tokens[i + 1].start + 1] == '{':
            if token.value in {'Gizmo', 'Group'}:
                if started and depth == 0:
                    raise ValueError(msg('gizmo.multiple_roots'))
                depth += 1
                started = True
            elif started and depth == 0:
                raise ValueError(msg('gizmo.outside_definition'))
    if depth:
        raise ValueError(msg('gizmo.missing_end'))


def code_dependencies(text):
    """Flag executable callbacks that static collection cannot prove portable."""
    from wrapitupiso.engine.core import node_blocks, knobs, words
    from wrapitupiso.engine.callback_scan import is_ui_callback
    reasons = []
    callbacks = {'onCreate', 'onScriptLoad', 'knobChanged', 'updateUI', 'autolabel',
                 'beforeRender', 'afterRender', 'beforeFrameRender', 'afterFrameRender',
                 'onDestroy', 'onScriptSave', 'onScriptClose'}
    for cls, pos, body, parents in node_blocks(text, scoped=True):
        values = knobs(body, pos)
        name = values['name'].value if 'name' in values else cls
        location = ' / '.join((*parents, name))
        for key in sorted(callbacks):
            if key in values and values[key].value.strip():
                token = values[key]
                if not is_ui_callback(text[token.start:token.end]):
                    reasons.append(msg('script.callback', location=location, knob=key))
        for key, token in values.items():
            if key in callbacks or key == 'addUserKnob':
                continue
            commands = re.findall(r'\[\s*([^\s\[\]{}"]+)', token.value)
            if any(command not in {'value', 'file', 'frame', 'expr', 'exists', 'metadata',
                                   'lrange', 'lindex', 'llength', 'split', 'join', 'list', 'string', 'format'}
                   for command in commands):
                reasons.append(msg('script.expression', location=location, knob=key))
        # addUserKnob may repeat, so do not use the knob dictionary for declarations.
        tokens = list(words(body))
        for a, b in zip(tokens, tokens[1:]):
            if a.value != 'addUserKnob':
                continue
            declaration = list(words(b.value))
            if declaration and declaration[0].value == '22':
                button = declaration[1].value if len(declaration) > 1 else msg('label.unnamed')
                reasons.append(msg('script.button', location=location, button=button))
    return list(dict.fromkeys(reasons))


def scan_dependencies(plan, cancel, progress, *, context=None, media_index=None):
    from wrapitupiso.engine.core import COMMON_CLASSES, Cancelled, _scan_media, node_blocks, knobs, is_gizmo_reference
    from wrapitupiso.engine.plugin_paths import discover_paths
    plan.warnings.append(msg('validation.scope'))
    roots = default_paths()
    if plan.snapshot:
        for root in roots: plan.snapshot.watch(root)
    # A missing default ~/.nuke is normal on a clean machine.
    roots = [p for p in roots if p != Path.home() / '.nuke' or p.exists()]
    found, active, custom_knobs = {}, [], {}
    plan.documents[str(plan.script)] = (plan.text, plan.encoding, hashlib.sha256(plan.text.encode(plan.encoding)).hexdigest())
    def inspect_code(text, owner):
        from wrapitupiso.engine.script_scan import inspect_commands
        commands, uncertain = inspect_commands(text)
        reviews = [fmt('{value0} / {value1}', value0=owner, value1=reason) for reason in code_dependencies(text) + uncertain]
        plan.script_warnings.extend(reviews)
        if plan.automatic_plugins:
            plan.warnings.extend((msg('script.preserved_review', reason=reason) for reason in reviews))
        blockers = [fmt('{value0}：{value1}', value0=owner, value1=reason) for reason in commands]
        return blockers if plan.automatic_plugins else blockers + reviews

    plan.plugin_errors.extend(inspect_code(plan.text, plan.script.name))

    def references(text):
        for cls, pos, body in node_blocks(text):
            if cls not in COMMON_CLASSES:
                name = knobs(body, pos).get('name')
                yield cls, name.value if name else cls

    root_references = list(references(plan.text))
    if not root_references:
        plan.warnings.extend(plan.plugin_errors)
        return
    if context:
        roots, index, index_errors, plan.plugin_snapshot = context.plugin_index(roots, cancel, progress)
    else:
        discovered = discover_paths(roots, cancel, progress)
        roots = discovered.paths
        index, index_errors = index_plugins(roots, cancel, progress)
        index_errors.extend(discovered.errors)
    plan.search_paths = [str(p) for p in roots]
    plan.plugin_search_errors = index_errors

    def visit(name, owner):
        if cancel():
            raise Cancelled()
        if name in found:
            plugin = found[name]
            if owner not in plugin.used_by:
                plugin.used_by.append(owner)
            if name in active:
                plugin.status = msg('status.needs_review')
                plugin.note = msg('gizmo.cycle') + ' → '.join(active + [name])
            return
        plugin = Plugin(name, used_by=[owner])
        found[name] = plugin
        plan.plugins.append(plugin)
        if re.fullmatch(r'OFX[A-Za-z_]\w*(?:\.[\w-]+)+', name):
            plugin.status = msg('status.external')
            plugin.note = msg('plugin.ofx_external')
            return
        explicit = is_gizmo_reference(name)
        if not explicit and any(p.name != name and p.name.casefold() == name.casefold() for p in plan.plugins):
            plugin.status, plugin.note = msg('status.conflict'), msg('gizmo.case_conflict')
            return
        if not explicit and not re.fullmatch(r'[A-Za-z_][\w.-]*', name):
            plugin.status, plugin.note = msg('status.needs_review'), msg('gizmo.unsafe_name')
            return
        if explicit:
            if any(char in name for char in '[]$\n\r;') or not Path(name).is_absolute():
                plugin.note = msg('gizmo.dynamic_reference')
                return
            source = Path(name).resolve()
            plugin.source = str(source)
            if plan.snapshot:
                plan.snapshot.watch(source, content=True)
                plan.snapshot.watch(source.parent)
            try:
                candidates = [source] if source.is_file() else []
            except OSError as error:
                plugin.status, plugin.note = msg('status.needs_review'), msg('gizmo.explicit_access', error=error_message(error))
                return
        else:
            candidates = index.get(name.casefold(), [])
        if plan.snapshot:
            for candidate in candidates:
                plan.snapshot.watch(candidate, content=candidate.suffix.lower() == '.gizmo')
        plugin.candidates = [str(p) for p in candidates]
        if not candidates:
            if explicit:
                plugin.note = msg('gizmo.explicit_missing')
                return
            plugin.note = (msg('plugin.missing_skipped')
                           if plan.automatic_plugins else
                           msg('plugin.missing'))
            return
        binary = next((p for p in candidates if p.parent == candidates[0].parent and p.suffix.lower() != '.gizmo'), None)
        if binary is not None:
            plugin.status, plugin.source = msg('status.external'), str(binary)
            plugin.note = msg('plugin.binary_external')
            if any(p.suffix.lower() == '.gizmo' for p in candidates):
                plugin.note += msg('plugin.binary_priority')
            return
        if any(p.suffix.lower() != '.gizmo' for p in candidates):
            plan.warnings.append(msg('plugin.lower_binary', name=name, path=candidates[0]))
            candidates = [p for p in candidates if p.suffix.lower() == '.gizmo']
        try:
            hashes = {digest_file(p, cancel) for p in candidates}
        except OSError as error:
            plugin.status, plugin.note = msg('status.needs_review'), msg('plugin.read_error', error=error_message(error))
            return
        if len(hashes) != 1:
            if not plan.automatic_plugins:
                plugin.status, plugin.note = msg('status.conflict'), msg('plugin.different_contents')
                return
            plan.warnings.append(msg('plugin.auto_select', name=name, path=candidates[0]))
        source = candidates[0]
        # One physical document may be referenced by both a class and a path.
        source = next((Path(owner) for owner in plan.documents if Path(owner) == source), source)
        plugin.source = str(source)
        if len(active) >= 100:
            plugin.status, plugin.note = msg('status.needs_review'), msg('gizmo.depth')
            return
        try:
            nested = _scan_media(source, cancel, base_override=plan.base, snapshot=plan.snapshot, media_index=media_index)
            validate_definition(nested.text)
            plugin.sha256 = hashlib.sha256(nested.text.encode(nested.encoding)).hexdigest()
            plan.documents[str(source)] = (nested.text, nested.encoding, plugin.sha256)
            custom_knobs[name] = file_knobs(nested.text)
            plugin.status, plugin.note = msg('status.ready'), msg('plugin.ready_note')
            reasons = inspect_code(nested.text, source.name)
            if reasons:
                plugin.status, plugin.note = msg('status.needs_review'), join_messages(msg('common.note_separator'), reasons)
            active.append(name)
            for child, node in references(nested.text):
                if child not in plugin.dependencies:
                    plugin.dependencies.append(child)
                visit(child, fmt('{value0} / {value1}', value0=name, value1=node))
            active.pop()
        except (OSError, ValueError) as error:
            plugin.status, plugin.note = msg('status.needs_review'), msg('gizmo.parse_error', error=error_message(error))

    for name, node in root_references:
        visit(name, fmt('{value0} / {value1}', value0=plan.script.name, value1=node))
    occupied = {p.name.casefold() for p in plan.plugins if not is_gizmo_reference(p.name)}
    for plugin in plan.plugins:
        if not is_gizmo_reference(plugin.name):
            plugin.packed_name = plugin.name.lower()
            continue
        if plugin.source not in plan.documents:
            continue
        stem = re.sub(r'[^A-Za-z0-9_]', '_', Path(plugin.source).stem)[:48].lower() or 'gizmo'
        suffix = hashlib.sha256(os.path.normcase(plugin.source).encode('utf-8')).hexdigest()[:12]
        base = fmt('wui_{value0}_{value1}', value0=stem, value1=suffix)
        alias, counter = base, 2
        while alias.casefold() in occupied:
            alias = fmt('{value0}_{value1}', value0=base, value1=counter)
            counter += 1
        occupied.add(alias.casefold())
        plugin.packed_name = alias
    # Rescan with the file controls declared by the collected Gizmo definitions.
    # This includes overrides saved on each instance, not only template defaults.
    rescanned = _scan_media(plan.script, cancel, extra_path_knobs=custom_knobs, snapshot=plan.snapshot, media_index=media_index)
    if hashlib.sha256(rescanned.text.encode(rescanned.encoding)).hexdigest() != plan.documents[str(plan.script)][2]:
        plan.plugin_errors.append(msg('scan.project_changed'))
    plan.assets = rescanned.assets
    scanned_sources = set()
    for plugin in plan.plugins:
        if plugin.source not in plan.documents:
            continue
        if plugin.source in scanned_sources:
            continue
        scanned_sources.add(plugin.source)
        mapping = dict(custom_knobs)
        try:
            nested = _scan_media(plugin.source, cancel, base_override=plan.base, extra_path_knobs=mapping, snapshot=plan.snapshot, media_index=media_index)
            if hashlib.sha256(nested.text.encode(nested.encoding)).hexdigest() != plugin.sha256:
                raise ValueError(msg('scan.plugin_changed'))
            for asset in nested.assets:
                asset.node = fmt('{value0} / {value1}', value0=plugin.name, value1=asset.node)
                asset.node_path = fmt('{value0}_{value1}', value0=plugin.name, value1=asset.node_path)
            plan.assets.extend(nested.assets)
            if any((status_code(a.status) != 'ready' for a in nested.assets)):
                plugin.status = msg('status.needs_review')
                plugin.note += msg('gizmo.internal_media')
        except (OSError, ValueError) as error:
            plugin.status, plugin.note = msg('status.needs_review'), error_message(error)
    # A file override on an instance is also part of that plugin's dependency set.
    if any((status_code(a.status) != 'ready' and a.node_class in found for a in plan.assets)):
        plan.plugin_errors.append(msg('gizmo.custom_paths'))
    changed = True
    while changed:
        changed = False
        for plugin in plan.plugins:
            children = [name for name in plugin.dependencies if status_code(found[name].status) not in {'ready', 'external'}
                        and not (plan.automatic_plugins and status_code(found[name].status) == 'not_found')]
            if status_code(plugin.status) == 'ready' and children:
                plugin.status = msg('status.needs_review')
                plugin.note = msg('gizmo.internal_plugins') + '、'.join(children)
                changed = True
    if any((status_code(plugin.status) != 'external' for plugin in plan.plugins)):
        (plan.warnings if plan.automatic_plugins else plan.plugin_errors).extend(index_errors)
    else:
        plan.warnings.extend(index_errors)
    for plugin in plan.plugins:
        if status_code(plugin.status) != 'ready':
            plan.warnings.append(fmt('{value0} × {value1}：{value2}；{value3}', value0=plugin.name, value1=len(plugin.used_by), value2=plugin.status, value3=plugin.note))
    plan.warnings.extend(plan.plugin_errors)


def reference_edits(text, plugins, *, aliases=None):
    from wrapitupiso.engine.core import node_blocks
    if aliases is None:
        aliases = {p.name: p.packed_name for p in plugins if status_code(p.status) == 'ready' and p.packed_name}
    return [(token.start, token.end, aliases[name])
            for name, _, _, token in node_blocks(text, class_tokens=True) if name in aliases]


def check_sources(plan, cancel):
    from wrapitupiso.engine.core import DependencyError
    for path, (_, _, digest) in plan.documents.items():
        if digest_file(path, cancel) != digest:
            raise DependencyError(msg('verify.source_changed', path=path))


def finish_bundle(plan, stage, edits, hashes, cancel, verify_progress=None, *, absolute_edits=None, aliases=None):
    from wrapitupiso.engine.core import DependencyError, Cancelled
    records = []
    if plan.plugins:
        folder = stage / 'gizmos'
        folder.mkdir()
        for plugin in plan.plugins:
            if cancel():
                raise Cancelled()
            if status_code(plugin.status) != 'ready':
                records.append({'name': plugin.name, 'original_reference': plugin.name, 'source': plugin.source, 'packed_path': None, 'status': msg('status.external') if status_code(plugin.status) == 'external' else msg('status.not_collected'), 'note': plugin.note, 'dependencies': plugin.dependencies, 'used_by': plugin.used_by, 'candidates': plugin.candidates})
                continue
            text, encoding, _ = plan.documents[plugin.source]
            for start, end, value in sorted(edits.get(plugin.source, []), reverse=True):
                text = text[:start] + value + text[end:]
            path = folder / ((plugin.packed_name or plugin.name) + '.gizmo')
            path.write_bytes(text.encode(encoding))
            absolute_path = None
            if absolute_edits is not None:
                from wrapitupiso.engine.delivery import apply_edits
                absolute_path = folder / (aliases[plugin.name] + '.gizmo')
                absolute_path.write_bytes(apply_edits(plan.documents[plugin.source][0], absolute_edits.get(plugin.source, [])).encode(encoding))
            records.append({'name': plugin.name, 'original_reference': plugin.name, 'packed_class': plugin.packed_name or plugin.name, 'source': plugin.source, 'source_sha256': plugin.sha256, 'packed_path': path.relative_to(stage).as_posix(), 'status': msg('status.collected'), 'absolute_packed_path': absolute_path.relative_to(stage).as_posix() if absolute_path else None, 'absolute_packed_class': aliases[plugin.name] if absolute_path else None, 'dependencies': plugin.dependencies, 'used_by': plugin.used_by, 'candidates': plugin.candidates})
    check_sources(plan, cancel)
    # A single destination read both verifies the copy and supplies the report hash.
    paths = {stage / relative for relative in hashes}
    for path in stage.rglob('*'):
        if cancel(): raise Cancelled()
        if path.is_file() and path.name != 'packaging_incomplete.txt': paths.add(path)
    total = len(paths)
    if verify_progress: verify_progress(0, total, msg('phase.start_verify'))
    for done, path in enumerate(sorted(paths)):
        relative = path.relative_to(stage).as_posix()
        expected = hashes.get(relative)
        if verify_progress:
            size = path.stat().st_size
            actual = digest_file(path, cancel, lambda count: verify_progress(
                done + min(count / size, 0.999) if size else done, total, path.name))
        else:
            actual = digest_file(path, cancel)
        if expected is not None and actual != expected:
            raise DependencyError(msg('verify.failed', path=relative))
        hashes[relative] = actual
        if verify_progress: verify_progress(done + 1, total, path.name)
    return {'plugins': records, 'gizmo_dependencies_complete': not plan.plugin_blockers
            and not plan.plugin_search_errors and not plan.script_warnings
            and all((status_code(p.status) == 'ready' for p in plan.plugins)),
            'script_dependencies_verified': False,
            'script_dependency_validation': 'static_only_runtime_not_verified',
            'script_dependency_warnings': plan.script_warnings,
            'plugin_search_paths': plan.search_paths, 'file_hashes': hashes,
            'nuke_runtime_verified': False}
