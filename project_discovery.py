"""Cancellable directory discovery. No Tk calls are made from this worker."""
import os
from pathlib import Path
import threading


class DiscoveryWorker(threading.Thread):
    def __init__(self, paths, events):
        super().__init__(daemon=False)
        self.paths = list(paths)
        self.events = events
        self.cancelled = threading.Event()

    def emit(self, kind, *values):
        if kind == 'discovered':
            while self.events.qsize() >= 20:
                if self.cancelled.wait(0.02): return
        self.events.put((kind, values))

    def run(self):
        visited, found, batch = set(), set(), []
        def add(path):
            path = path.resolve()
            if path.suffix.lower() == '.nk' and path not in found:
                found.add(path)
                batch.append(path)
                if len(batch) >= 50:
                    self.emit('discovered', batch.copy())
                    batch.clear()
        def failed(error):
            self.emit('message', f'无法读取目录 · {error.filename}', 'error')
        try:
            for source in self.paths:
                if self.cancelled.is_set(): break
                source = Path(source).resolve()
                try:
                    if not source.is_dir():
                        if source.is_file(): add(source)
                        else: self.emit('message', f'找不到文件 · {source.name}', 'warning')
                        continue
                    count = len(found)
                    already_visited = source in visited
                    for directory, dirs, files in os.walk(source, onerror=failed, followlinks=False):
                        if self.cancelled.is_set(): break
                        current = Path(directory).resolve()
                        if current in visited:
                            dirs[:] = []
                            continue
                        visited.add(current)
                        dirs[:] = sorted(d for d in dirs if d not in {'.git', '__pycache__'})
                        for name in sorted(files):
                            if self.cancelled.is_set(): break
                            if name.lower().endswith('.nk'): add(current / name)
                        # Publish each directory immediately, without filling the queue per file.
                        if batch:
                            self.emit('discovered', batch.copy())
                            batch.clear()
                    if len(found) == count and not already_visited and not self.cancelled.is_set():
                        self.emit('message', f'未找到 .nk · {source.name}', 'warning')
                except OSError as error:
                    failed(error)
        finally:
            if batch: self.emit('discovered', batch)
            self.emit('discovery_finished', len(found), self.cancelled.is_set())
