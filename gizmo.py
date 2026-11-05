"""Static Gizmo dependency collection. Never import a source init/menu script."""
from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import re


@dataclass
class Plugin:
    name: str
    status: str = '未找到'
    source: str = ''
    candidates: list = field(default_factory=list)
    note: str = ''
    used_by: list = field(default_factory=list)
    dependencies: list = field(default_factory=list)
    sha256: str = ''


def digest_file(path, cancel=lambda: False, progress=None):
    from core import Cancelled
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
    from core import Cancelled
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
        progress(0, 0, f'查找插件：{root.name}')
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
            errors.append(f'插件目录读取失败：{error.filename or root}')
    return index, errors


def file_knobs(text):
    """File controls and link controls forwarding a known file knob."""
    from core import node_blocks, words, PATH_KNOBS
    result = set()
    for cls, pos, body in node_blocks(text):
        # Only the outer definition declares knobs visible on Gizmo instances.
        tokens = list(words(body))
        for a, b in zip(tokens, tokens[1:]):
            if a.value != 'addUserKnob':
                continue
            declaration = [t.value for t in words(b.value)]
            if len(declaration) < 2:
                continue
            if declaration[0] == '2':
                result.add(declaration[1])
            elif declaration[0] == '41' and 'T' in declaration:
                index = declaration.index('T') + 1
                if index < len(declaration) and declaration[index].split('.')[-1] in PATH_KNOBS:
                    result.add(declaration[1])
        break
    return result


def validate_definition(text):
    from core import node_blocks, words
    blocks = list(node_blocks(text))
    if not blocks or blocks[0][0] not in {'Gizmo', 'Group'}:
        raise ValueError('没有 Gizmo/Group 定义')
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
                raise ValueError('多余的 end_group')
        elif i + 1 < len(tokens) and text[tokens[i + 1].start:tokens[i + 1].start + 1] == '{':
            if token.value in {'Gizmo', 'Group'}:
                if started and depth == 0:
                    raise ValueError('包含多个顶层定义')
                depth += 1
                started = True
            elif started and depth == 0:
                raise ValueError('节点位于 Gizmo 定义之外')
    if depth:
        raise ValueError('缺少 end_group，Gizmo 定义未结束')


def code_dependencies(text):
    """Flag executable callbacks that static collection cannot prove portable."""
    from core import node_blocks, knobs, words
    from callback_scan import is_ui_callback
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
                    reasons.append(f'{location}：含 {key} 回调，需核对脚本依赖')
        # addUserKnob may repeat, so do not use the knob dictionary for declarations.
        tokens = list(words(body))
        for a, b in zip(tokens, tokens[1:]):
            if a.value != 'addUserKnob':
                continue
            declaration = list(words(b.value))
            if declaration and declaration[0].value == '22':
                reasons.append('含 Python/Tcl 按钮，需核对脚本依赖')
    if re.search(r'(?m)^\s*(?:python|source|load|load_plugin)\s', text):
        reasons.append('含外部脚本加载命令')
    return list(dict.fromkeys(reasons))


def scan_dependencies(plan, cancel, progress, *, context=None, media_index=None):
    from core import COMMON_CLASSES, Cancelled, _scan_media, node_blocks, knobs
    from plugin_paths import discover_paths
    plan.warnings.append('已检查显式 Gizmo 依赖；二进制插件兼容性、隐式 OCIO 和渲染结果仍需在 Nuke 中核对。')
    roots = default_paths()
    if plan.snapshot:
        for root in roots: plan.snapshot.watch(root)
    # A missing default ~/.nuke is normal on a clean machine.
    roots = [p for p in roots if p != Path.home() / '.nuke' or p.exists()]
    found, active, custom_knobs = {}, [], {}
    plan.documents[str(plan.script)] = (plan.text, plan.encoding, hashlib.sha256(plan.text.encode(plan.encoding)).hexdigest())
    plan.plugin_errors.extend(code_dependencies(plan.text))

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
                plugin.status = '待核对'
                plugin.note = '循环 Gizmo 依赖：' + ' → '.join(active + [name])
            return
        plugin = Plugin(name, used_by=[owner])
        found[name] = plugin
        plan.plugins.append(plugin)
        if re.fullmatch(r'OFX[A-Za-z_]\w*(?:\.[\w-]+)+', name):
            plugin.status = '外部依赖'
            plugin.note = 'OFX 插件不参与打包；接收方需自行安装兼容版本并配置授权。'
            return
        if any(p.name != name and p.name.casefold() == name.casefold() for p in plan.plugins):
            plugin.status, plugin.note = '同名冲突', '节点名称仅大小写不同，无法安全写入同一插件目录'
            return
        if not re.fullmatch(r'[A-Za-z_][\w.-]*', name):
            plugin.status, plugin.note = '待核对', '节点名称无法安全映射为 Gizmo 文件名'
            return
        candidates = index.get(name.casefold(), [])
        if plan.snapshot:
            for candidate in candidates:
                plan.snapshot.watch(candidate, content=candidate.suffix.lower() == '.gizmo')
        plugin.candidates = [str(p) for p in candidates]
        if not candidates:
            plugin.note = ('未找到对应文件，跳过收集并继续打包；也可能是未收录的内置节点。'
                           if plan.automatic_plugins else
                           '未找到对应文件；也可能是未收录的内置节点。')
            return
        if any(p.suffix.lower() != '.gizmo' for p in candidates):
            plugin.status, plugin.note = '待核对', '发现二进制插件，无法证明版本、授权及关联库完整；不自动打包'
            return
        try:
            hashes = {digest_file(p, cancel) for p in candidates}
        except OSError as error:
            plugin.status, plugin.note = '待核对', f'插件文件读取失败：{error}'
            return
        if len(hashes) != 1:
            if not plan.automatic_plugins:
                plugin.status, plugin.note = '同名冲突', '同名文件内容不同'
                return
            plan.warnings.append(f'{name}：同名版本不同，按搜索顺序自动选择 {candidates[0]}')
        source = candidates[0]
        plugin.source = str(source)
        if len(active) >= 100:
            plugin.status, plugin.note = '待核对', 'Gizmo 嵌套超过 100 层，请检查依赖'
            return
        try:
            nested = _scan_media(source, cancel, base_override=plan.base, snapshot=plan.snapshot, media_index=media_index)
            validate_definition(nested.text)
            plugin.sha256 = hashlib.sha256(nested.text.encode(nested.encoding)).hexdigest()
            plan.documents[str(source)] = (nested.text, nested.encoding, plugin.sha256)
            custom_knobs[name] = file_knobs(nested.text)
            plugin.status, plugin.note = '就绪', '已找到，静态依赖已扫描'
            reasons = code_dependencies(nested.text)
            if reasons:
                plugin.status, plugin.note = '待核对', '；'.join(reasons)
            active.append(name)
            for child, node in references(nested.text):
                if child not in plugin.dependencies:
                    plugin.dependencies.append(child)
                visit(child, f'{name} / {node}')
            active.pop()
        except (OSError, ValueError) as error:
            plugin.status, plugin.note = '待核对', f'Gizmo 解析失败：{error}'

    for name, node in root_references:
        visit(name, f'{plan.script.name} / {node}')
    # Rescan with the file controls declared by the collected Gizmo definitions.
    # This includes overrides saved on each instance, not only template defaults.
    rescanned = _scan_media(plan.script, cancel, extra_path_knobs=custom_knobs, snapshot=plan.snapshot, media_index=media_index)
    if hashlib.sha256(rescanned.text.encode(rescanned.encoding)).hexdigest() != plan.documents[str(plan.script)][2]:
        plan.plugin_errors.append('扫描过程中工程发生变化，请重新扫描')
    plan.assets = rescanned.assets
    for plugin in plan.plugins:
        if plugin.source not in plan.documents:
            continue
        mapping = dict(custom_knobs)
        mapping.update({'Gizmo': custom_knobs[plugin.name], 'Group': custom_knobs[plugin.name]})
        try:
            nested = _scan_media(plugin.source, cancel, base_override=plan.base, extra_path_knobs=mapping, snapshot=plan.snapshot, media_index=media_index)
            if hashlib.sha256(nested.text.encode(nested.encoding)).hexdigest() != plugin.sha256:
                raise ValueError('扫描过程中插件内容变化')
            for asset in nested.assets:
                asset.node = f'{plugin.name} / {asset.node}'
                asset.node_path = f'{plugin.name}_{asset.node_path}'
            plan.assets.extend(nested.assets)
            if any(a.status != '就绪' for a in nested.assets):
                plugin.status = '待核对'
                plugin.note += '；内部素材存在缺失、缺帧或动态路径'
        except (OSError, ValueError) as error:
            plugin.status, plugin.note = '待核对', str(error)
    # A file override on an instance is also part of that plugin's dependency set.
    if any(a.status != '就绪' and a.node_class in found for a in plan.assets):
        plan.plugin_errors.append('Gizmo 自定义文件参数存在缺失或动态路径')
    changed = True
    while changed:
        changed = False
        for plugin in plan.plugins:
            children = [name for name in plugin.dependencies if found[name].status not in {'就绪', '外部依赖'}
                        and not (plan.automatic_plugins and found[name].status == '未找到')]
            if plugin.status == '就绪' and children:
                plugin.status = '待核对'
                plugin.note = '内部插件依赖未解决：' + '、'.join(children)
                changed = True
    if any(plugin.status != '外部依赖' for plugin in plan.plugins):
        (plan.warnings if plan.automatic_plugins else plan.plugin_errors).extend(index_errors)
    else:
        plan.warnings.extend(index_errors)
    for plugin in plan.plugins:
        if plugin.status != '就绪':
            plan.warnings.append(f'{plugin.name} × {len(plugin.used_by)}：{plugin.status}；{plugin.note}')
    plan.warnings.extend(plan.plugin_errors)


def check_sources(plan, cancel):
    from core import DependencyError
    for path, (_, _, digest) in plan.documents.items():
        if digest_file(path, cancel) != digest:
            raise DependencyError(f'扫描后文件发生变化，请重新扫描：{path}')


def finish_bundle(plan, stage, edits, hashes, cancel, verify_progress=None):
    from core import DependencyError, Cancelled
    records = []
    if plan.plugins:
        folder = stage / 'gizmos'
        folder.mkdir()
        for plugin in plan.plugins:
            if cancel():
                raise Cancelled()
            if plugin.status != '就绪':
                records.append({'name': plugin.name, 'source': plugin.source, 'packed_path': None,
                                'status': '外部依赖' if plugin.status == '外部依赖' else '未收集',
                                'note': plugin.note, 'dependencies': plugin.dependencies,
                                'used_by': plugin.used_by, 'candidates': plugin.candidates})
                continue
            text, encoding, _ = plan.documents[plugin.source]
            for start, end, value in sorted(edits.get(plugin.source, []), reverse=True):
                text = text[:start] + value + text[end:]
            path = folder / (plugin.name + '.gizmo')
            path.write_bytes(text.encode(encoding))
            records.append({'name': plugin.name, 'source': plugin.source, 'source_sha256': plugin.sha256,
                            'packed_path': path.relative_to(stage).as_posix(), 'status': '已收集',
                            'dependencies': plugin.dependencies, 'used_by': plugin.used_by,
                            'candidates': plugin.candidates})
        write_loader(plan, stage)
    check_sources(plan, cancel)
    # A single destination read both verifies the copy and supplies the report hash.
    paths = {stage / relative for relative in hashes}
    for path in stage.rglob('*'):
        if cancel(): raise Cancelled()
        if path.is_file() and path.name != 'PACKAGING_INCOMPLETE.txt': paths.add(path)
    total = len(paths)
    if verify_progress: verify_progress(0, total, '开始校验')
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
            raise DependencyError(f'打包文件校验失败：{relative}')
        hashes[relative] = actual
        if verify_progress: verify_progress(done + 1, total, path.name)
    return {'plugins': records, 'gizmo_dependencies_complete': not plan.plugin_blockers
            and not plan.plugin_search_errors and all(p.status == '就绪' for p in plan.plugins),
            'plugin_search_paths': plan.search_paths, 'file_hashes': hashes,
            'nuke_runtime_verified': False}


def write_loader(plan, stage):
    # Paths are Python literals, never interpolated into shell commands.
    loader = '''from pathlib import Path
import hashlib
import json
import nuke

root = Path(__file__).resolve().parent
report = json.loads((root / 'report.json').read_text(encoding='utf-8'))
for relative, expected in report['file_hashes'].items():
    path = (root / relative).resolve()
    if root not in path.parents:
        raise RuntimeError('Invalid package path: ' + relative)
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            digest.update(chunk)
    if digest.hexdigest() != expected:
        raise RuntimeError('Package checksum failed: ' + relative)
nuke.pluginAddPath(str(root / 'gizmos'))
'''
    loader += f'nuke.scriptOpen(str(root / {plan.script.name!r}))\n'
    (stage / 'load_package.py').write_text(loader, encoding='utf-8')
    version = re.search(r'(?m)^version\s+(\d+\.\d+)', plan.text)
    desired = version.group(1) if version else ''
    powershell = '''param([string]$NukeExe)
$ErrorActionPreference = 'Stop'
try {
    if (-not $NukeExe) { $NukeExe = $env:NUKE_EXECUTABLE }
    if (-not $NukeExe) {
        $candidates = @(Get-ChildItem -LiteralPath $env:ProgramFiles -Directory -Filter 'Nuke*' |
            ForEach-Object { Get-ChildItem -LiteralPath $_.FullName -File -Filter 'Nuke*.exe' } |
            Where-Object { $_.BaseName -match '^Nuke[0-9]+[.][0-9]+$' })
        $matching = @($candidates | Where-Object { $_.BaseName -eq 'NukeDESIRED_VERSION' })
        if ($matching.Count -eq 1) { $NukeExe = $matching[0].FullName }
        elseif ($candidates.Count -eq 1) { $NukeExe = $candidates[0].FullName }
    }
    if (-not $NukeExe) {
        Add-Type -AssemblyName System.Windows.Forms
        $dialog = New-Object System.Windows.Forms.OpenFileDialog
        $dialog.Title = 'Select the Nuke executable for this project'
        $dialog.Filter = 'Nuke executable|Nuke*.exe'
        if ($dialog.ShowDialog() -ne 'OK') { exit 1 }
        $NukeExe = $dialog.FileName
    }
    if (-not (Test-Path -LiteralPath $NukeExe -PathType Leaf)) { throw 'Nuke executable not found' }
    $start = New-Object System.Diagnostics.ProcessStartInfo
    $start.FileName = $NukeExe
    # Some Nuke versions misdecode non-ASCII command-line filenames on Windows.
    # Keep the argument ASCII; the working directory retains the Unicode path.
    $start.Arguments = 'NUKE_ARGUMENTS'
    $start.WorkingDirectory = $PSScriptRoot
    $start.UseShellExecute = $false
    $process = [System.Diagnostics.Process]::Start($start)
    $process.WaitForExit()
    exit $process.ExitCode
} catch {
    Write-Host $_ -ForegroundColor Red
    Read-Host 'Press Enter to close'
    exit 1
}
'''.replace('DESIRED_VERSION', desired).replace(
        'NUKE_ARGUMENTS', 'load_package.py' if any(p.status == '外部依赖' for p in plan.plugins)
        else '--safe load_package.py')
    (stage / 'launch.ps1').write_text(powershell, encoding='utf-8-sig')
    (stage / '打开工程.cmd').write_bytes(b'@echo off\r\npowershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch.ps1" %*\r\n')
