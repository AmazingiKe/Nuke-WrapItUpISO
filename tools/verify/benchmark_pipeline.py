"""Compare a committed baseline with the working tree on generated local fixtures.

Usage: python tools/verify/benchmark_pipeline.py --baseline-ref c728eef
No production projects are read or modified. Times include warm filesystem caches.
"""
import argparse
import importlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from zipfile import ZipFile
from unittest.mock import patch


def measure(code, fixture, output, optimized):
    sys.path.insert(0, str(code))
    prefix = 'wrapitupiso.engine.' if (code / 'wrapitupiso').is_dir() else ''
    core = importlib.import_module(prefix + 'core')
    gizmo = importlib.import_module(prefix + 'gizmo')
    counts = {'index_builds': 0, 'media_hash_reads': 0, 'media_hash_bytes': 0}
    index_plugins, digest_file = gizmo.index_plugins, gizmo.digest_file
    def index(*args, **kwargs):
        counts['index_builds'] += 1
        return index_plugins(*args, **kwargs)
    def digest(path, *args, **kwargs):
        if output in Path(path).parents and 'media' in Path(path).parts:
            counts['media_hash_reads'] += 1
            counts['media_hash_bytes'] += Path(path).stat().st_size
        return digest_file(path, *args, **kwargs)
    scripts = sorted((fixture / 'scripts').glob('*.nk'))
    started = time.perf_counter()
    with patch.object(gizmo, 'default_paths', return_value=[fixture / 'plugins']), \
            patch.object(gizmo, 'index_plugins', side_effect=index), patch.object(gizmo, 'digest_file', side_effect=digest):
        if optimized:
            session = importlib.import_module(prefix + 'scan_cache').ScanSession()
            for script in scripts: session.scan(script)
        else:
            for script in scripts: core.scan(script, automatic_plugins=True)
        scanned = time.perf_counter()
        for script in scripts:
            plan = session.scan(script, reuse=True) if optimized else core.scan(script, automatic_plugins=True)
            core.package(plan, output)
    counts.update(scan_seconds=round(scanned-started, 3),
                  package_seconds=round(time.perf_counter()-scanned, 3),
                  total_seconds=round(time.perf_counter()-started, 3))
    print(json.dumps(counts))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-ref')
    parser.add_argument('--measure', nargs=4, metavar=('CODE', 'FIXTURE', 'OUTPUT', 'OPTIMIZED'))
    parser.add_argument('--result', type=Path)
    args = parser.parse_args()
    if args.measure:
        code, fixture, output, optimized = args.measure
        measure(Path(code), Path(fixture), Path(output), optimized == 'yes')
        return
    if not args.baseline_ref: parser.error('请指定 --baseline-ref')
    root = Path(__file__).resolve().parents[2]
    scratch = root / 'tools' / 'build'
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='benchmark-', dir=scratch) as temp:
        temporary = Path(temp).resolve()
        assert temporary.parent == scratch.resolve()
        baseline, fixture = temporary / 'baseline', temporary / 'fixture'
        baseline.mkdir(); fixture.mkdir()
        archive = temporary / 'baseline.zip'
        subprocess.run(['git', 'archive', '--format=zip', f'--output={archive}', args.baseline_ref], cwd=root, check=True)
        with ZipFile(archive) as files:
            for member in files.infolist():
                if not (baseline / member.filename).resolve().is_relative_to(baseline):
                    raise ValueError('Baseline archive contains a path outside its directory')
            files.extractall(baseline)
        scripts, media, plugins = (fixture / name for name in ('scripts', 'source_media', 'plugins'))
        for path in (scripts, media, plugins): path.mkdir()
        (media / 'plate.exr').write_bytes(b'x' * (16 * 1024 * 1024))
        for index in range(300):
            directory = plugins / f'category{index:03}'
            directory.mkdir()
            (directory / f'Dummy{index}.gizmo').write_text('Gizmo {\n}\nend_group\n')
        (plugins / 'init.py').write_text('import nuke\n' + ''.join(
            f"nuke.pluginAppendPath('category{index:03}')\n" for index in range(300)))
        (plugins / 'Demo.gizmo').write_text('Gizmo {\n}\nend_group\n')
        for index in range(8):
            (scripts / f'shot{index}.nk').write_text('Read {\n file ../source_media/plate.exr\n}\nDemo {\n}\n')
        results = {'fixture': '8 projects, 301 plugin directories, 16 MiB media per project; local warm-cache timings'}
        for label, code, optimized in [('baseline', baseline, 'no'), ('optimized', root, 'yes')]:
            command = [sys.executable, str(Path(__file__).resolve()), '--measure', str(code),
                       str(fixture), str(temporary / label / 'output'), optimized]
            results[label] = json.loads(subprocess.check_output(command, cwd=root, text=True))
        if args.result:
            args.result.write_text(json.dumps(results, indent=2), encoding='utf-8')
        print(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
