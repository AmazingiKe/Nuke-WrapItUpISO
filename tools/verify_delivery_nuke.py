"""Render generated font/media fixtures before and after packaging with Nuke."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import package, scan
from delivery import path_word


def verify(nuke, font):
    with tempfile.TemporaryDirectory(prefix='wrapitupiso_delivery_') as temporary:
        root = Path(temporary)
        source = root / 'source'
        source.mkdir()
        fonts = source / 'source fonts'
        fonts.mkdir()
        custom_font = fonts / 'PackageOnlyArial.ttf'
        shutil.copyfile(font, custom_font)
        script = source / 'delivery.nk'
        probe = root / 'probe.py'
        path_fixture = root / 'literal_path.nk'
        path_fixture.write_text('Root {\n inputs 0\n}\nNoOp {\n inputs 0\n name PathProbe\n addUserKnob {2 file}\n file '
                                + path_word('C:/literal [folder]/$value/plate.exr') + '\n}\n', encoding='utf-8')
        startup = None
        if sys.platform == 'win32':
            startup = subprocess.STARTUPINFO()
            startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow = 0

        def run(code):
            probe.write_text(code, encoding='utf-8')
            result = subprocess.run([str(nuke), '--safe', '-ti', str(probe)], cwd=root,
                                    capture_output=True, timeout=90, startupinfo=startup)
            output = (result.stdout + result.stderr).decode(errors='replace')
            if result.returncode or 'Traceback' in output or 'ERROR:' in output:
                raise RuntimeError(output)

        run(f'''import nuke
nuke.root()['format'].setValue(nuke.addFormat('256 128 1 delivery_verify'))
nuke.root()['free_type_font_path'].setValue({fonts.as_posix()!r})
nuke.root()['free_type_system_fonts'].setValue(False)
modern = nuke.nodes.Text2(name='Modern', message='WrapItUpISO', font_size=32, box='0 0 256 128')
modern['font'].fromScript('{{ Arial : Regular : PackageOnlyArial.ttf : 0 }}')
legacy = nuke.nodes.Text(name='Legacy', message='WrapItUpISO', font={custom_font.as_posix()!r}, size=32, box='0 0 256 128', yjustify='center')
legacy.setInput(0, nuke.nodes.Constant(color='0 0 0 0'))
nuke.scriptSaveAs({script.as_posix()!r}, overwrite=1)
''')
        # An internal font reference must be linked in both Gizmo variants too.
        gizmo = source / 'FontGizmo.gizmo'
        gizmo.write_text('Gizmo {\n inputs 0\n}\nConstant {\n inputs 0\n color {0 0 0 0}\n}\nText {\n font "' + custom_font.as_posix()
                         + '"\n message WrapItUpISO\n size 32\n box {0 0 256 128}\n yjustify center\n name Internal\n}\n'
                         'Output {\n name Output1\n}\nend_group\n', encoding='utf-8')
        with script.open('a', encoding='utf-8') as stream:
            stream.write('FontGizmo {\n inputs 0\n name Nested\n}\n')

        def render(path, plugins, label):
            destination = root / label
            destination.mkdir()
            run(f'''import nuke, json
from pathlib import Path
nuke.scriptOpen({str(path_fixture)!r})
assert nuke.toNode('PathProbe')['file'].evaluate() == 'C:/literal [folder]/$value/plate.exr'
nuke.scriptClear()
nuke.pluginAddPath({str(plugins)!r})
try:
    nuke.scriptOpen({str(path)!r})
except Exception:
    print('FONT_DIAGNOSTIC', nuke.root().name(), nuke.root()['free_type_font_path'].value(), list((Path({str(path)!r}).parent / 'fonts').iterdir()))
    raise
result = {{}}
for name in ('Modern', 'Legacy', 'Nested'):
    node = nuke.toNode(name)
    writer = nuke.nodes.Write(inputs=[node], file={destination.as_posix()!r} + '/' + name + '.png', file_type='png', channels='rgba')
    nuke.execute(writer, 1, 1)
    assert not node.error(), name
    alpha = sum(node.sample('alpha', x, y) for x in range(0, node.width(), 8) for y in range(0, node.height(), 8))
    assert alpha > 0, name
    result[name] = {{'alpha_samples': alpha}}
Path({str(destination / 'result.json')!r}).write_text(json.dumps(result))
''')
            return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in destination.glob('*.png')}

        original_bytes = script.read_bytes()
        before = render(script, source, 'before')
        with patch('gizmo.default_paths', return_value=[source]):
            plan = scan(script, automatic_plugins=True)
            assert all(a.status == '就绪' for a in plan.assets), [(a.raw, a.status, a.note) for a in plan.assets]
            target = package(plan, root / 'output')
        report = json.loads((target / 'report.json').read_text(encoding='utf-8'))
        assert (target / report['original_path_script']).read_bytes() == original_bytes
        absolute = render(target / report['relinked_script'], target / 'gizmos', 'absolute')
        assert before == absolute and len(before) == 3, (before, absolute)
        moved = root / 'relocated delivery'
        assert root == source.resolve().parent and root in target.resolve().parents
        target.rename(moved)
        shutil.rmtree(source)  # Only our generated fixture, inside TemporaryDirectory.
        relative = render(moved / report['relative_script'], moved / 'gizmos', 'relative')
        assert before == relative, (before, relative)
        unicode_target = root / '中文 relocated delivery'
        moved.rename(unicode_target)
        unicode_supported = True
        try:
            unicode_render = render(unicode_target / report['relative_script'], unicode_target / 'gizmos', 'unicode')
            assert unicode_render == before, (unicode_render, before)
        except RuntimeError as error:
            if 'Font (' not in str(error) or 'not available' not in str(error):
                raise
            unicode_supported = False
        return {'rendered_nodes': list(before), 'absolute_matches_source': True,
                'relocated_relative_matches_source': True, 'original_bytes_preserved': True,
                'text2_unicode_font_directory_supported': unicode_supported,
                'font_files': [p.name for p in (unicode_target / 'fonts').iterdir()]}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--nuke', type=Path, required=True)
    parser.add_argument('--font', type=Path, default=Path('C:/Windows/Fonts/arial.ttf'))
    args = parser.parse_args()
    print(json.dumps(verify(args.nuke, args.font), ensure_ascii=True, indent=2))
