"""Compare static search/sequence rules and delivered pixels with an installed Nuke.

Only generated startup scripts are executed, using a disposable user profile.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import scan, package
from gizmo import default_paths
from plugin_paths import discover_paths
from scan_cache import ScanSession


def verify(nuke):
    with tempfile.TemporaryDirectory(prefix='nuke_scan_verify_') as temporary:
        root = Path(temporary).resolve()
        sources = root/'sources'; sources.mkdir()
        paths = {name:sources/name for name in ('R1','R2',*'ABCDEFGHXY')}
        for path in paths.values(): path.mkdir()
        home = sources/'profile'; (home/'.nuke').mkdir(parents=True)
        def config(name, calls):
            (paths[name]/'init.py').write_text('import nuke\n' + ''.join(
                f'nuke.plugin{kind}Path({str(paths[child])!r})\n' for kind,child in calls))
        config('R1', [('Add','A'),('Add','B'),('Append','C'),('Append','D')])
        config('R2', [('Add','E'),('Add','F'),('Append','G'),('Append','H')])
        config('A', [('Add','X')]); config('B', [('Add','Y')])
        modern = sources / 'PathStartup'
        for name in ('Direct', 'Shelves/A', 'Shelves/B', 'Shelves/.ignored',
                     'Recursive/A/C', 'Recursive/B', 'Walk/A/C', 'Walk/B'):
            (modern / name).mkdir(parents=True, exist_ok=True)
        (modern / 'init.py').write_text('''import os, nuke
from pathlib import Path
base = Path(__file__).resolve().parent
for folder in [base / 'Direct']:
    if folder.is_dir(): nuke.pluginAddPath(folder.as_posix())
shelves = base / 'Shelves'
if shelves.exists() and shelves.is_dir():
    for folder in sorted(shelves.iterdir(), key=lambda p: p.name.lower()):
        if not folder.is_dir() or folder.name.startswith('.'):
            continue
        nuke.pluginAppendPath(folder.resolve(strict=False).as_posix())
def register(root):
    for folder in root.rglob('*'):
        if folder.is_dir(): nuke.pluginAddPath(folder.as_posix())
def main():
    register(base / 'Recursive')
if __name__ == '__main__': main()
dirname, filename = os.path.split(os.path.abspath(__file__))
for root, dirs, files in os.walk(os.path.join(dirname, 'Walk')):
    nuke.pluginAppendPath(root)
''', encoding='utf-8')
        backup = paths['R1']/'backup'; backup.mkdir()
        def gizmo(folder, name, color):
            (folder/(name+'.gizmo')).write_text(f'Gizmo {{\n inputs 0\n name {name}\n}}\nConstant {{\n inputs 0\n color {{{color}}}\n}}\nOutput {{\n}}\nend_group\n')
        gizmo(paths['A'], 'ReviewChoice', '1 0 0 1')
        gizmo(paths['B'], 'ReviewChoice', '0 1 0 1')
        gizmo(backup, 'ReviewArchive', '0 1 0 1')
        gizmo(paths['R2'], 'ReviewArchive', '1 0 0 1')
        script = sources/'shot.nk'
        script.write_text('version 15.1 v5\nRoot {\n}\nReviewChoice {\n inputs 0\n name Choice1\n}\nReviewArchive {\n inputs 0\n name Archive1\n}\n')
        frames = sources/'frames'; frames.mkdir()
        def chunk(kind, data):
            return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind+data))
        png = (b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',1,1,8,6,0,0,0))
               +chunk(b'IDAT',zlib.compress(b'\0\xff\0\0\xff'))+chunk(b'IEND',b''))
        patterns = ('%d','%04d','%4d','#','##','####')
        frame_numbers = (-10,-1,1,10000)
        for pattern in patterns:
            fmt = '%0'+str(len(pattern))+'d' if pattern.startswith('#') else pattern
            for frame in frame_numbers: (frames/('plate.'+(fmt % frame)+'.png')).write_bytes(png)
        for bad in ('plate.00001.png','plate.-0001.png'): (frames/bad).write_bytes(png)
        env = dict(os.environ, HOME=str(home), USERPROFILE=str(home),
                   NUKE_PATH=os.pathsep.join([str(paths[p]) for p in ('R1','R2')] + [str(modern)]))
        env.pop('PYTHONPATH',None); env.pop('PYTHONHOME',None)
        reference = root/'reference.json'
        probe = root/'probe.py'
        probe.write_text(f'''import nuke,json
from pathlib import Path
result = {{'paths': [p for p in nuke.pluginPath() if Path({str(sources)!r}) in Path(p).parents]}}
result['pixels'] = {{}}
for cls in ('ReviewChoice','ReviewArchive'):
    node=nuke.createNode(cls,inpanel=False)
    result['pixels'][cls]=[node.sample('red',0,0),node.sample('green',0,0)]
result['frames']={{}}
knob=nuke.File_Knob('testfile')
for pattern in {patterns!r}:
    knob.setValue({frames.as_posix()!r}+'/plate.'+pattern+'.png')
    result['frames'][pattern]=[]
    for frame in {frame_numbers!r}:
        context=nuke.OutputContext();context.setFrame(frame)
        result['frames'][pattern].append(knob.getEvaluatedValue(context))
Path({str(reference)!r}).write_text(json.dumps(result))
''', encoding='utf-8')
        info = subprocess.STARTUPINFO()
        info.dwFlags |= subprocess.STARTF_USESHOWWINDOW; info.wShowWindow=0
        def run(probe, safe):
            command=[str(nuke)]+(['--safe'] if safe else [])+['-ti',str(probe)]
            result=subprocess.run(command,env=env,cwd=root,capture_output=True,timeout=60,startupinfo=info)
            if result.returncode: raise RuntimeError((result.stdout+result.stderr).decode(errors='replace'))
        run(probe, False)
        actual=json.loads(reference.read_text())
        with patch.dict(os.environ,env,clear=True):
            discovered=discover_paths(default_paths())
            assert discovered.paths == [Path(p) for p in actual['paths']], (discovered.paths,actual['paths'])
            session=ScanSession(); plan=session.scan(script)
            assert session.scan(script,reuse=True) is plan
            assert Path(plan.plugins[0].source) == paths['A']/'ReviewChoice.gizmo'
            assert Path(plan.plugins[1].source) == paths['R2']/'ReviewArchive.gizmo'
            target=package(plan,root/'delivery')
            for pattern, filenames in actual['frames'].items():
                sequence=sources/'sequence.nk'
                sequence.write_text(f'Read {{\n file frames/plate.{pattern}.png\n first 1\n last 1\n}}\n')
                asset=scan(sequence).assets[0]
                assert set(asset.files) == {Path(p) for p in filenames}, (pattern,asset.files,filenames)
        moved=root/'中文 moved package'
        assert root in target.resolve().parents and root in moved.resolve().parents
        target.rename(moved)
        assert sources.resolve().parent == root
        shutil.rmtree(sources)
        delivered=root/'delivered.json'
        probe.write_text(f'''import nuke,json
from pathlib import Path
nuke.pluginAddPath({str(moved/'gizmos')!r})
nuke.scriptOpen({str(moved/script.name)!r})
result={{}}
for cls,name in (('ReviewChoice','Choice1'),('ReviewArchive','Archive1')):
    node=nuke.toNode(name)
    result[cls]=[node.sample('red',0,0),node.sample('green',0,0)]
Path({str(delivered)!r}).write_text(json.dumps(result))
''',encoding='utf-8')
        run(probe, True)
        pixels=json.loads(delivered.read_text())
        assert pixels == actual['pixels'] == {'ReviewChoice':[1.0,0.0],'ReviewArchive':[1.0,0.0]}, pixels
        return {'search_order':[Path(p).name for p in actual['paths']],
                'sequence_patterns':list(patterns),'frames':list(frame_numbers),
                'source_pixels':actual['pixels'],'relocated_package_pixels':pixels}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--nuke',type=Path,required=True)
    parser.add_argument('--result',type=Path)
    args=parser.parse_args()
    result=verify(args.nuke)
    text=json.dumps(result,ensure_ascii=False,indent=2)
    if args.result: args.result.write_text(text,encoding='utf-8')
    print(text)
