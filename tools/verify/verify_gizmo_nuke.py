"""Integration check using an installed Nuke; only executes generated fixtures.

python tools/verify/verify_gizmo_nuke.py --nuke "C:/Program Files/Nuke15.1v5/Nuke15.1.exe" [--gui]
"""
import argparse
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from wrapitupiso.engine.core import package
from wrapitupiso.engine.scan_cache import ScanSession


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
        (source / 'Outer.gizmo').write_text('Gizmo {\n inputs 0\n name Outer\n}\n"'
                                          + (source / 'Inner.gizmo').as_posix()
                                          + '" {\n inputs 0\n name Inner1\n}\nOutput {\n name Output1\n}\nend_group\n')
        script = source / 'test.nk'
        script.write_text('version 15.1 v5\nRoot {\n}\nOuter {\n inputs 0\n name Outer1\n}\n'
                          'Group {\n inputs 0\n name InlineFiles\n addUserKnob {2 texture}\n texture texture.png\n}\nend_group\n'
                          + '"' + (source / 'Outer.gizmo').as_posix() + '" {\n inputs 0\n name PathOuter1\n}\n')
        with patch('wrapitupiso.engine.gizmo.default_paths', return_value=[source]):
            session = ScanSession()
            plan = session.scan(script)
            assert session.scan(script, reuse=True) is plan
            target = package(plan, root / 'delivery')
            expected_class = next(p.packed_name for p in plan.plugins if p.name == (source / 'Outer.gizmo').as_posix())
        moved = root / '中文 moved package'
        target.rename(moved)
        assert root.resolve() in source.resolve().parents
        shutil.rmtree(source)  # Only this generated fixture, inside TemporaryDirectory.
        # Test-only probe follows the documented manual setup. Nothing executable
        # is added to the delivered package.
        loader = root / 'probe.py'
        loader.write_text(f'''import nuke, json
from pathlib import Path
root = Path({str(moved)!r})
nuke.pluginAddPath(str(root / 'gizmos'))
nuke.scriptOpen(str(root / 'test_relinked_relative.nk'))
''' + '''
node = nuke.toNode('Outer1')
result = {'gui': nuke.env['gui'], 'class': node.Class(),
          'red': node.sample('red', 0, 0), 'green': node.sample('green', 0, 0),
          'file': nuke.toNode('Outer1.Inner1.Read1')['file'].evaluate(),
          'inline_file': nuke.toNode('InlineFiles')['texture'].evaluate(),
          'inline_knob_class': nuke.toNode('InlineFiles')['texture'].Class(),
          'path_class': nuke.toNode('PathOuter1').Class(),
          'path_red': nuke.toNode('PathOuter1').sample('red', 0, 0)}
(root / 'validation_result.json').write_text(json.dumps(result))
if nuke.env['gui']:
    nuke.root().setModified(False)
    nuke.scriptExit()
''', encoding='utf-8')
        command = [str(nuke), '--safe'] + ([] if gui else ['-ti']) + ['probe.py']
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
        result = subprocess.run(command, cwd=root, capture_output=True, timeout=90, startupinfo=startup)
        output = (result.stdout + result.stderr).decode('utf-8', errors='replace')
        if result.returncode:
            raise RuntimeError(f'Nuke exit {result.returncode}: {output}')
        data = json.loads((moved / 'validation_result.json').read_text())
        assert data['class'] == 'outer', data
        assert data['gui'] == gui, data
        assert abs(data['red'] - 1) < 0.0001 and abs(data['green']) < 0.0001, data
        assert Path(data['file']).is_relative_to(moved), data
        assert Path(data['inline_file']).is_relative_to(moved) and Path(data['inline_file']).is_file(), data
        assert data['inline_knob_class'] == 'File_Knob', data
        assert data['path_class'] == expected_class and abs(data['path_red'] - 1) < 0.0001, data
        print(json.dumps(data, ensure_ascii=True))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--nuke', type=Path, required=True)
    parser.add_argument('--gui', action='store_true')
    args = parser.parse_args()
    verify(args.nuke, args.gui)
