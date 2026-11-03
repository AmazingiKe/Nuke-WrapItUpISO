"""Collect .nk assets without importing Nuke or executing script expressions."""
from __future__ import annotations
import os, re, json, hashlib, shutil, stat
from pathlib import Path
from dataclasses import dataclass, field

class Cancelled(Exception): pass

class DependencyError(RuntimeError): pass

@dataclass
class Token:
    value: str
    start: int
    end: int

def words(text):
    """Tcl-style words with balanced braces/quotes, retaining original spans."""
    i, n = 0, len(text)
    while i < n:
        if text[i].isspace(): i += 1; continue
        if text[i] == '#' and (i == 0 or text[i-1] == '\n'):
            j = text.find('\n', i); i = n if j < 0 else j+1; continue
        start = i
        if text[i] in '{"':
            opener = text[i]; i += 1; depth = 1
            while i < n:
                if text[i] == '\\': i += 2; continue
                if opener == '{' and text[i] == '{': depth += 1
                if text[i] == ('}' if opener == '{' else '"'):
                    depth -= 1
                    if depth == 0: i += 1; break
                i += 1
            if depth: raise ValueError('工程包含未闭合的括号或引号')
        else:
            while i < n and not text[i].isspace():
                i += 2 if text[i] == '\\' else 1
        raw = text[start:i]
        value = raw[1:-1] if raw.startswith(('{','"')) else raw
        yield Token(value, start, i)

def node_blocks(text):
    # Nuke serializes nodes as Class { body }. Group children are subsequent nodes.
    tokens = list(words(text))
    for a, b in zip(tokens, tokens[1:]):
        line_start = text.rfind('\n', 0, a.start)+1
        if text[line_start:a.start].strip(): continue
        if a.value in {'define_window_layout_xml','add_layer','set','push','version','end_group'}: continue
        if re.fullmatch(r'[A-Za-z_][\w:.-]*', a.value) and text[b.start:b.start+1] == '{':
            yield a.value, b.start+1, b.value

def knobs(body, offset):
    result = {}
    for t in words(body):
        # Only words starting at the beginning of a physical knob line are keys.
        line_start = body.rfind('\n', 0, t.start)+1
        if body[line_start:t.start].strip(): continue
        if not re.fullmatch(r'[\w]+', t.value): continue
        rest_start = t.end
        while rest_start < len(body) and body[rest_start] in ' \t': rest_start += 1
        if rest_start >= len(body) or body[rest_start] in '\r\n': continue
        value = next(words(body[rest_start:]), None)
        if value:
            result[t.value] = Token(value.value, offset+rest_start+value.start, offset+rest_start+value.end)
    return result

INPUT_CLASSES = {'Read','DeepRead','ReadGeo','ReadGeo2','Camera2','Camera3','Axis2','Axis3','ReadGeo3'}
PATH_KNOBS = {'file','proxy','vfield_file','lut_file','cdl_file','ocio_config','font_file'}
SEQUENCE = re.compile(r'%0?([1-9]\d*)?d|#+|<UDIM>')
# This list suppresses noise only; it is not a claim that dependencies are verified.
COMMON_CLASSES = INPUT_CLASSES | set('''Root Group Write DeepWrite Viewer BackdropNode
Transform Merge2 Grade Blur Shuffle Shuffle2 Dot NoOp Reformat Crop Roto RotoPaint
Constant ColorCorrect Switch TimeOffset TimeWarp FrameHold Dissolve Premult Unpremult
Retime Colorspace Input Output Remove Keyer Expression ShuffleCopy Cryptomatte
Multiply Keymix HueCorrect Copy Denoise2 Defocus AddChannels Invert Grain2 ColorLookup
NoTimeBlur AdjBBox CopyBBox STMap TimeBlur Clamp Saturation Gamma Exposure Add Plus
FilterErode Erode Dilate EdgeBlur Sharpen VectorBlur ZDefocus2 MotionBlur ScanlineRender
Scene Card2 Cube Sphere Cylinder Camera Axis ReadGeo MergeGeo TransformGeo Project3D
ApplyMaterial BasicMaterial OCIOColorSpace OCIOFileTransform OCIOCDLTransform
OCIODisplay OCIOLogConvert OCIOLookTransform OCIOColorSpaceTransform
FrameRange AppendClip Kronos OFlow IDistort Displace SplineWarp3 GridWarp3 LensDistortion2
UnrealReader DeepMerge DeepToImage DeepFromImage DeepCrop DeepRecolor DeepHoldout
DeepExpression DeepTransform DeepColorCorrect DeepSample DeepShuffle
Gizmo BlinkScript ChannelMerge ClipTest ColorMatrix Difference Log2Lin Matrix
MergeExpression Noise Position Ramp SoftClip StickyNote CheckerBoard CheckerBoard2
ColorWheel Text Text2 ColorBars CurveTool Histogram ContactSheet Switch
Vectorfield VectorDistort VectorGenerator HueShift HSVTool GodRays Glow Flare
Convolve Median Bilateral Merge EdgeDetect Posterize'''.split())

@dataclass
class Asset:
    node: str
    knob: str
    raw: str
    start: int
    end: int
    source: str = ''
    files: list = field(default_factory=list)
    status: str = ''
    note: str = ''
    missing: list = field(default_factory=list)
    owner: str = ''
    node_class: str = ''

@dataclass
class Plan:
    script: Path
    text: str
    encoding: str
    assets: list
    warnings: list
    plugins: list = field(default_factory=list)
    plugin_errors: list = field(default_factory=list)
    documents: dict = field(default_factory=dict)
    search_paths: list = field(default_factory=list)
    base: Path | None = None
    @property
    def size(self):
        return sum(p.stat().st_size for p in {p for a in self.assets for p in a.files}) + sum(
            Path(p.source).stat().st_size for p in self.plugins if p.source and p.status == '就绪')

    @property
    def plugin_blockers(self):
        return self.plugin_errors + [f'{p.name}：{p.status}；{p.note}' for p in self.plugins if p.status != '就绪']

def read_script(path):
    data = path.read_bytes()
    for enc in (('utf-8-sig' if data.startswith(b'\xef\xbb\xbf') else 'utf-8'),'gb18030'):
        try: return data.decode(enc), enc
        except UnicodeDecodeError: pass
    raise ValueError('无法解码工程，请转换为 UTF-8 后重试')

def resolve(raw, base):
    if any(c in raw for c in '[]\n\r'):
        raise ValueError('动态表达式：未执行，请手动处理')
    value = os.path.expandvars(raw).replace('\\', '/')
    if '$' in value or re.search(r'%[A-Za-z_]\w*%', value):
        raise ValueError('环境变量未定义')
    if any(c in value for c in '{}";'):
        raise ValueError('特殊路径语法，需手动处理')
    p = Path(value).expanduser()
    if not p.is_absolute() and base is None:
        raise ValueError('project_directory 无法解析，相对素材路径需手动处理')
    return p if p.is_absolute() else base / p

def _scan_media(path, cancel=lambda: False, progress=lambda a,b,c: None, *, base_override=..., extra_path_knobs=None):
    path = Path(path).resolve()
    text, enc = read_script(path)
    blocks = [(cls, knobs(body, pos)) for cls, pos, body in node_blocks(text)]
    progress(0, len(blocks), '解析工程完成')
    base, warnings = path.parent, []
    for cls, k in blocks:
        if cancel(): raise Cancelled()
        if cls == 'Root' and k.get('project_directory') and k['project_directory'].value:
            try: base = resolve(k['project_directory'].value, path.parent).resolve()
            except ValueError as e:
                base = None
                warnings.append('project_directory: '+str(e))
    if base_override is not ...:
        base = base_override
    assets = []
    for index, (cls, k) in enumerate(blocks, 1):
        if cancel(): raise Cancelled()
        name = k.get('name', Token(cls,0,0)).value
        for key, t in k.items():
            if key not in PATH_KNOBS | (extra_path_knobs or {}).get(cls, set()) or not t.value: continue
            if cls in {'Write','DeepWrite'}: continue
            if key == 'file' and cls in {'Camera2','Camera3','Axis2','Axis3','ReadGeo','ReadGeo2','ReadGeo3'}:
                if k.get('read_from_file', Token('false',0,0)).value not in {'true','1'}: continue
            a = Asset(name,key,t.value,t.start,t.end,owner=str(path),node_class=cls); assets.append(a)
            try:
                source = resolve(t.value, base).resolve(); a.source = str(source)
                matches = list(SEQUENCE.finditer(source.name))
                if len(matches) > 1: raise ValueError('多个序列占位符，需手动处理')
                if matches:
                    m = matches[0]; tok = m.group(); width = (int(m.group(1)) if m.group(1) else None) if tok.startswith('%') else (4 if tok == '<UDIM>' else len(tok))
                    digits = r'\d+' if width is None else rf'\d{{{width}}}'
                    pattern = re.compile(re.escape(source.name[:m.start()])+f'(-?{digits})'+re.escape(source.name[m.end():]))
                    numbers = []
                    if source.parent.is_dir():
                        for p in sorted(source.parent.iterdir()):
                            if cancel(): raise Cancelled()
                            found = pattern.fullmatch(p.name)
                            if found and p.is_file(): a.files.append(p); numbers.append(int(found.group(1)))
                    if numbers and tok != '<UDIM>':
                        first = int(k.get('first',Token(str(min(numbers)),0,0)).value)
                        last = int(k.get('last',Token(str(max(numbers)),0,0)).value)
                        if last-first <= 1000000: a.missing = sorted(set(range(first,last+1))-set(numbers))
                    a.note = '收集目录中所有匹配帧'+(f'；声明范围缺 {len(a.missing)} 帧' if a.missing else '')
                elif source.is_file(): a.files = [source]
                a.status = '缺帧' if a.missing else ('就绪' if a.files else '缺失')
            except (ValueError,OSError) as e: a.status = '待处理'; a.note = str(e)
        progress(index, len(blocks), name)
    return Plan(path,text,enc,assets,list(dict.fromkeys(warnings)),base=base)


def scan(path, cancel=lambda: False, progress=lambda a,b,c: None, *, plugin_paths=None, plugin_overrides=None):
    from gizmo import scan_dependencies
    plan = _scan_media(path, cancel, progress)
    scan_dependencies(plan, plugin_paths, plugin_overrides or {}, cancel, progress)
    return plan

def package(plan, output, cancel=lambda: False, progress=lambda a,b,c: None, byte_progress=None):
    from gizmo import check_sources, finish_bundle
    if plan.plugin_blockers:
        raise DependencyError('插件依赖未解决，未生成交付包：\n' + '\n'.join(plan.plugin_blockers))
    check_sources(plan, cancel)
    output = Path(output).resolve(); output.mkdir(parents=True, exist_ok=True)
    tag = hashlib.sha256(str(plan.script).encode()).hexdigest()[:10]
    name = re.sub(r'[^\w.-]', '_', plan.script.stem)+'_'+tag
    target = output/name
    try: target.mkdir()
    except FileExistsError: raise FileExistsError(f'输出已存在，请选择新目录或移走：{target}')
    # Windows may deny renaming a large directory even after all copies succeeded.
    # Reserve the final name immediately; the marker guards unfinished packages.
    stage = target
    edits, copied, rows, hashes = {}, set(), [], {}
    total = len({p for a in plan.assets for p in a.files}); done = 0
    try:
        marker = stage/'PACKAGING_INCOMPLETE.txt'
        marker.write_text('打包尚未完成，请勿交付此目录。\n',encoding='utf-8')
        copied_bytes = 0
        total_bytes = 0
        if byte_progress is not None:
            for source_file in {p for a in plan.assets for p in a.files}:
                if cancel(): raise Cancelled()
                total_bytes += source_file.stat().st_size
            byte_progress(0, total_bytes, '准备复制')
        for a in plan.assets:
            if cancel(): raise Cancelled()
            row = {'node':a.node,'knob':a.knob,'original':a.raw,'status':a.status,'note':a.note,'missing_frames':a.missing,'files':len(a.files),'document':a.owner}
            rows.append(row)
            if not a.files: continue
            folder = hashlib.sha256(os.path.normcase(str(Path(a.source).parent)).encode()).hexdigest()[:16]
            dest = stage/'media'/folder; dest.mkdir(parents=True,exist_ok=True)
            for p in a.files:
                if p in copied: continue
                destination = dest/p.name
                succeeded = False
                try:
                    digest = hashlib.sha256()
                    before = p.stat()
                    with p.open('rb') as src, destination.open('xb') as dst:
                        while True:
                            if cancel(): raise Cancelled()
                            chunk = src.read(4*1024*1024)
                            if not chunk: break
                            dst.write(chunk)
                            digest.update(chunk)
                            copied_bytes += len(chunk)
                            if byte_progress is not None:
                                byte_progress(copied_bytes, total_bytes, p.name)
                    # Preserve timestamps without copying Windows read-only flags.
                    metadata = p.stat()
                    if (before.st_size, before.st_mtime_ns) != (metadata.st_size, metadata.st_mtime_ns):
                        raise DependencyError(f'复制过程中素材发生变化，请重新扫描：{p}')
                    os.utime(destination,(metadata.st_atime,metadata.st_mtime))
                    hashes[destination.relative_to(stage).as_posix()] = digest.hexdigest()
                    succeeded = True
                finally:
                    if not succeeded: destination.unlink(missing_ok=True)
                copied.add(p); done += 1; progress(done,total,p.name)
            rel = f'media/{folder}/{Path(a.source).name}'
            # Quoted Tcl word retains command substitution for relocatable script path.
            new = '"[file dirname [value root.name]]/'+rel+'"'
            edits.setdefault(a.owner or str(plan.script), []).append((a.start,a.end,new)); row['packed_path'] = rel
        text = plan.text
        for start,end,value in sorted(edits.get(str(plan.script), []),reverse=True): text = text[:start]+value+text[end:]
        with (stage/plan.script.name).open('w',encoding=plan.encoding,newline='') as f: f.write(text)
        plugin_report = finish_bundle(plan, stage, edits, hashes, cancel)
        report = {'source_script':str(plan.script),'copied_files':done,'explicit_media_complete':all(a.status=='就绪' for a in plan.assets),'dependency_validation':'static_only_runtime_not_verified','warnings':plan.warnings,'assets':rows, **plugin_report}
        (stage/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        (stage/'打包说明.txt').write_text('有 Gizmo 时请双击 打开工程.cmd，或使用 Nuke 执行 load_package.py；不要直接双击 .nk。\n移动时请搬走整个文件夹。\n仅完成静态依赖收集与文件校验，尚不代表 Nuke 加载或渲染验证通过。\n素材待处理、缺失、缺帧及其他限制请查看 report.json。\n',encoding='utf-8')
        if cancel(): raise Cancelled()
        marker.unlink()
        return target
    except BaseException as error:
        def remove_readonly(func,path,exc):
            os.chmod(path,stat.S_IWRITE|stat.S_IREAD|stat.S_IEXEC)
            func(path)
        try: shutil.rmtree(stage,onerror=remove_readonly)
        except OSError as cleanup_error:
            raise RuntimeError(f'{type(error).__name__}: {error}；未完成目录清理失败，请手动检查 {stage}：{cleanup_error}') from error
        raise
