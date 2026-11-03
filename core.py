"""Offline .nk collection. Never imports Nuke or executes script expressions."""
from __future__ import annotations
import os, re, json, hashlib, shutil, stat
from pathlib import Path
from dataclasses import dataclass, field

class Cancelled(Exception): pass

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
        if re.fullmatch(r'[A-Za-z_][\w:]*', a.value) and text[b.start:b.start+1] == '{':
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
DeepExpression DeepTransform DeepColorCorrect DeepSample DeepShuffle'''.split())

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

@dataclass
class Plan:
    script: Path
    text: str
    encoding: str
    assets: list
    warnings: list
    @property
    def size(self):
        return sum(p.stat().st_size for p in {p for a in self.assets for p in a.files})

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

def scan(path, cancel=lambda: False, progress=lambda a,b,c: None):
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
    assets, unknown = [], {}
    for index, (cls, k) in enumerate(blocks, 1):
        if cancel(): raise Cancelled()
        name = k.get('name', Token(cls,0,0)).value
        if cls not in COMMON_CLASSES and 'name' in k:
            unknown[cls] = unknown.get(cls, 0)+1
        for key, t in k.items():
            if key not in PATH_KNOBS or not t.value: continue
            if cls in {'Write','DeepWrite'}: continue
            if key == 'file' and cls in {'Camera2','Camera3','Axis2','Axis3','ReadGeo','ReadGeo2','ReadGeo3'}:
                if k.get('read_from_file', Token('false',0,0)).value not in {'true','1'}: continue
            a = Asset(name,key,t.value,t.start,t.end); assets.append(a)
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
    if unknown:
        warnings.append('未识别节点类型（不代表缺失插件）：'+ '、'.join(f'{cls} × {count}' for cls,count in sorted(unknown.items()))+'；离线模式无法验证其依赖。')
    warnings.append('离线模式不验证外部 Gizmo、二进制插件和隐式 OCIO 依赖；素材状态请查看列表。')
    return Plan(path,text,enc,assets,list(dict.fromkeys(warnings)))

def package(plan, output, cancel=lambda: False, progress=lambda a,b,c: None, byte_progress=None):
    output = Path(output).resolve(); output.mkdir(parents=True, exist_ok=True)
    tag = hashlib.sha256(str(plan.script).encode()).hexdigest()[:10]
    name = re.sub(r'[^\w.-]', '_', plan.script.stem)+'_'+tag
    target = output/name
    try: target.mkdir()
    except FileExistsError: raise FileExistsError(f'输出已存在，请选择新目录或移走：{target}')
    # Windows may deny renaming a large directory even after all copies succeeded.
    # Reserve the final name immediately; the marker guards unfinished packages.
    stage = target
    edits, copied, rows = [], set(), []
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
            row = {'node':a.node,'knob':a.knob,'original':a.raw,'status':a.status,'note':a.note,'missing_frames':a.missing,'files':len(a.files)}
            rows.append(row)
            if not a.files: continue
            folder = hashlib.sha256(os.path.normcase(str(Path(a.source).parent)).encode()).hexdigest()[:16]
            dest = stage/'media'/folder; dest.mkdir(parents=True,exist_ok=True)
            for p in a.files:
                if p in copied: continue
                destination = dest/p.name
                succeeded = False
                try:
                    with p.open('rb') as src, destination.open('xb') as dst:
                        while True:
                            if cancel(): raise Cancelled()
                            chunk = src.read(4*1024*1024)
                            if not chunk: break
                            dst.write(chunk)
                            copied_bytes += len(chunk)
                            if byte_progress is not None:
                                byte_progress(copied_bytes, total_bytes, p.name)
                    # Preserve timestamps without copying Windows read-only flags.
                    metadata = p.stat()
                    os.utime(destination,(metadata.st_atime,metadata.st_mtime))
                    succeeded = True
                finally:
                    if not succeeded: destination.unlink(missing_ok=True)
                copied.add(p); done += 1; progress(done,total,p.name)
            rel = f'media/{folder}/{Path(a.source).name}'
            # Quoted Tcl word retains command substitution for relocatable script path.
            new = '"[file dirname [value root.name]]/'+rel+'"'
            edits.append((a.start,a.end,new)); row['packed_path'] = rel
        text = plan.text
        for start,end,value in sorted(edits,reverse=True): text = text[:start]+value+text[end:]
        with (stage/plan.script.name).open('w',encoding=plan.encoding,newline='') as f: f.write(text)
        report = {'source_script':str(plan.script),'copied_files':done,'explicit_media_complete':all(a.status=='就绪' for a in plan.assets),'dependency_validation':'manual_required','warnings':plan.warnings,'assets':rows}
        (stage/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        (stage/'打包说明.txt').write_text('打开本目录中的 .nk 文件。移动时请搬走整个文件夹。\n待处理、缺失、缺帧及插件依赖请查看 report.json。\n',encoding='utf-8')
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
