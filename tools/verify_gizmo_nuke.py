"""Integration check using an installed Nuke; only executes generated fixtures.

python tools/verify_gizmo_nuke.py --nuke "C:/Program Files/Nuke15.1v5/Nuke15.1.exe" [--gui]
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import package
from scan_cache import ScanSession


def verify(nuke, gui=False):
    with tempfile.TemporaryDirectory(prefix='gizmo_verify_') as temporary:
        root = Path(temporary)
        source = root / 'source'
        source.mkdir()
        def chunk(kind, data):
            return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
        (source / 'texture.png').write_bytes(
            b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(b'\0\xff\0\0\xff')) + chunk(b'IEND', b''))
        (source / 'Inner.gizmo').write_text('Gizmo {\n inputs 0\n name Inner\n}\nRead {\n inputs 0\n file texture.png\n colorspace linear\n name Read1\n}\nOutput {\n name Output1\n}\nend_group\n')
        (source / 'Outer.gizmo').write_text('Gizmo {\n inputs 0\n name Outer\n}\nInner {\n inputs 0\n name Inner1\n}\nOutput {\n name Output1\n}\nend_group\n')
        script = source / 'test.nk'
        script.write_text('version 15.1 v5\nRoot {\n}\nOuter {\n inputs 0\n name Outer1\n}\n')
        with patch('gizmo.default_paths', return_value=[source]):
            session = ScanSession()
            plan = session.scan(script)
            assert session.scan(script, reuse=True) is plan
            target = package(plan, root / 'delivery')
        moved = root / '中文 moved package'
        target.rename(moved)
        assert root.resolve() in source.resolve().parents
        shutil.rmtree(source)  # Only this generated fixture, inside TemporaryDirectory.
        loader = moved / 'load_package.py'
        loader.write_text(loader.read_text(encoding='utf-8') + '''
node = nuke.toNode('Outer1')
result = {'gui': nuke.env['gui'], 'class': node.Class(),
          'red': node.sample('red', 0, 0), 'green': node.sample('green', 0, 0),
          'file': nuke.toNode('Outer1.Inner1.Read1')['file'].evaluate()}
(root / 'validation_result.json').write_text(json.dumps(result))
if nuke.env['gui']:
    nuke.root().setModified(False)
    nuke.scriptExit()
''', encoding='utf-8')
        report_file = moved / 'report.json'
        report = json.loads(report_file.read_text(encoding='utf-8'))
        report['file_hashes']['load_package.py'] = hashlib.sha256(loader.read_bytes()).hexdigest()
        report_file.write_text(json.dumps(report), encoding='utf-8')
        if gui:
            command = ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                       '-File', str(moved / 'launch.ps1'), '-NukeExe', str(nuke)]
        else:
            command = [str(nuke), '--safe', '-ti', 'load_package.py']
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
        result = subprocess.run(command, cwd=moved, capture_output=True, timeout=90, startupinfo=startup)
        output = (result.stdout + result.stderr).decode('utf-8', errors='replace')
        if result.returncode:
            raise RuntimeError(f'Nuke exit {result.returncode}: {output}')
        data = json.loads((moved / 'validation_result.json').read_text())
        assert data['class'] == 'Outer', data
        assert data['gui'] == gui, data
        assert abs(data['red'] - 1) < 0.0001 and abs(data['green']) < 0.0001, data
        assert Path(data['file']).is_relative_to(moved), data
        print(json.dumps(data, ensure_ascii=True))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--nuke', type=Path, required=True)
    parser.add_argument('--gui', action='store_true')
    args = parser.parse_args()
    verify(args.nuke, args.gui)
