"""Measure native Qt language switching with empty and populated windows."""
import argparse
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from wrapitupiso.config.i18n import message as msg
from wrapitupiso.engine.core import Asset, Plan
from wrapitupiso.ui.window import Window, ensure_application


def benchmark():
    app = ensure_application()
    app.setQuitOnLastWindowClosed(False)
    results = []
    reports = Path(__file__).resolve().parents[1] / 'reports'
    reports.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=reports) as directory:
        folder = Path(directory)
        for count, log_count in ((0, 0), (1000, 5000)):
            window = Window(settings_path=folder / 'wrapitupiso_settings.json', system_language='zh-CN')
            try:
                window.show()
                if count:
                    source = folder / 'benchmark.nk'
                    window.discovered([source])
                    assets = [Asset(f'Read{i}', 'file', f'plate{i}.exr', 1, 1, status=msg('status.ready')) for i in range(count)]
                    window.show_plan(Plan(source, '', 'utf-8', assets, []), 0)
                    window.log_entries = [(msg('log.scanned') + f' · shot{i}.nk', 'info') for i in range(log_count)]
                    window.write_lines(window.log, window.log_entries)
                app.processEvents()
                samples = []
                for language in ('en', 'ja', 'zh-CN') * 3:
                    start = time.perf_counter()
                    window.set_language(language)
                    updated = time.perf_counter()
                    app.processEvents()
                    painted = time.perf_counter()
                    samples.append({'language': language, 'switch_ms': (updated-start)*1000,
                                    'paint_ms': (painted-updated)*1000, 'total_ms': (painted-start)*1000})
                results.append({'assets': count, 'log_lines': log_count, 'samples': samples,
                                'median_switch_ms': statistics.median(s['switch_ms'] for s in samples),
                                'median_total_ms': statistics.median(s['total_ms'] for s in samples)})
            finally:
                window.close()
                window.deleteLater()
                app.processEvents()
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = benchmark()
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    for scenario in result:
        print({k: v for k, v in scenario.items() if k != 'samples'})
