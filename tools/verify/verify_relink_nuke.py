"""Verify manually replaced and folder-matched dependencies in real Nuke renders."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from wrapitupiso.engine.core import package, scan
from wrapitupiso.engine.relink import dependency_key, find_candidates, manual_path


def verify(nuke, font):
    root = Path(tempfile.mkdtemp(prefix='wrapitupiso_relink_'))
    search = root / 'search/deep'
    search.mkdir(parents=True)
    startup = subprocess.STARTUPINFO() if sys.platform == 'win32' else None
    if startup:
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
    def run(code):
        probe = root / 'probe.py'
        probe.write_text(code, encoding='utf-8')
        result = subprocess.run([str(nuke), '--safe', '-ti', str(probe)], cwd=root,
                                capture_output=True, timeout=90, startupinfo=startup)
        output = (result.stdout + result.stderr).decode(errors='replace')
        if result.returncode or 'Traceback' in output or 'ERROR:' in output: raise RuntimeError(output)
        return output
    run(f'''import nuke
nuke.root()['format'].setValue(nuke.addFormat('96 32 1 relink_verify'))
constant = nuke.nodes.Constant(color='0.6 0.2 0.1 1')
writer = nuke.nodes.Write(inputs=[constant], file={(search / 'plate.%04d.png').as_posix()!r}, file_type='png', channels='rgba')
nuke.execute(writer, 1001, 1003)
''')
    for frame in range(1001, 1004): shutil.copyfile(search / f'plate.{frame}.png', search / f'internal.{frame}.png')
    font_path = search / 'ReplacementArial.ttf'
    shutil.copyfile(font, font_path)
    gizmo = search / 'ManuallyChosen.gizmo'
    gizmo.write_text('Gizmo {\n inputs 0\n}\nRead {\n inputs 0\n file missing/internal.%04d.png\n first 1001\n last 1003\n name Internal\n}\nOutput {\n name Output1\n}\nend_group\n', encoding='utf-8')
    script = root / 'relink_fixture.nk'
    script.write_text('Root {\n format "96 32 0 0 96 32 1 relink_verify"\n first_frame 1001\n last_frame 1003\n}\nRead {\n inputs 0\n file missing/plate.####.png\n first 1001\n last 1003\n name Plate\n}\nMissingTool {\n inputs 0\n name Custom\n}\nText2 {\n inputs 0\n font {invalid}\n message Relink\n font_size 12\n box {0 0 96 32}\n name Title\n}\n', encoding='utf-8')
    original = script.read_bytes()
    with patch('wrapitupiso.engine.gizmo.default_paths', return_value=[]), patch('wrapitupiso.engine.font_scan.search_folders', return_value=([], [])):
        plan = scan(script, automatic_plugins=True)
        choices = {dependency_key(plan.assets[0]): manual_path(plan.assets[0], search / 'plate.1001.png'),
                   dependency_key(plan.plugins[0]): str(gizmo),
                   dependency_key(next(a for a in plan.assets if a.kind == 'font')): str(font_path)}
        plan = scan(script, automatic_plugins=True, replacements=choices)
        matches, errors = find_candidates(plan.assets, root / 'search')
        assert not errors
        choices.update({key: paths[0] for key, paths in matches.items() if len(paths) == 1})
        plan = scan(script, automatic_plugins=True, replacements=choices)
        assert not plan.plugin_blockers
        assert all(asset.files for asset in plan.assets)
        output = package(plan, root / 'delivery')
    assert script.read_bytes() == original
    assert (output / 'relink_fixture_original.nk').read_bytes() == original
    moved = root / 'moved_package'
    shutil.copytree(output, moved)
    results = []
    for label, folder, filename in (('absolute', output, 'relink_fixture_relinked.nk'),
                                    ('relative', moved, 'relink_fixture_relinked_relative.nk')):
        renders = root / ('render_' + label)
        renders.mkdir()
        run(f'''import nuke, json
from pathlib import Path
nuke.pluginAddPath({str(folder / 'gizmos')!r})
nuke.scriptOpen({str(folder / filename)!r})
values = {{}}
for name in ('Plate', 'Custom', 'Title'):
    node = nuke.toNode(name)
    assert node is not None, name
    writer = nuke.nodes.Write(inputs=[node], file={str(renders).replace(chr(92), '/')!r} + '/' + name + '.%04d.png', file_type='png', channels='rgba')
    nuke.execute(writer, 1001, 1003)
    assert not node.error(), name
    values[name] = node.Class()
assert 'replacementarial.ttf' in nuke.toNode('Title')['font'].toScript()
Path({str(renders / 'nodes.json')!r}).write_text(json.dumps(values))
''')
        images = sorted(renders.glob('*.png'))
        assert len(images) == 9
        results.append({'variant': label, 'nodes': json.loads((renders / 'nodes.json').read_text()),
                        'renders': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in images}})
    assert results[0]['renders'] == results[1]['renders']
    return {'passed': True, 'fixture': str(root), 'nuke': str(nuke), 'original_unchanged': True, 'results': results}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--nuke', type=Path, required=True)
    parser.add_argument('--font', type=Path, default=Path('C:/Windows/Fonts/arial.ttf'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = verify(args.nuke, args.font)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'passed': result['passed'], 'fixture': result['fixture'], 'rendered_images': 18}))
