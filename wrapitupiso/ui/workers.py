"""Scan and package jobs; communicate with the UI through queue events."""
import threading
import time

from wrapitupiso.config.i18n import status_code, message as msg, fmt, join_messages, error_message
from wrapitupiso.engine.core import Cancelled, DependencyError, package, scan
from wrapitupiso.engine.scan_cache import ScanSession


def readable_error(error):
    if isinstance(error, DependencyError):
        return msg('error.validation')
    if isinstance(error, FileExistsError):
        return msg('error.output_exists')
    if isinstance(error, FileNotFoundError):
        return msg('error.file_missing')
    if isinstance(error, PermissionError):
        return msg('error.access')
    if isinstance(error, ValueError):
        return msg('error.parse')
    if msg('error.cleanup_marker') in error_message(error):
        return msg('error.cleanup')
    if isinstance(error, OSError):
        return msg('error.io')
    return msg('error.failed')


def needs_review(plan):
    return bool(plan.plugin_blockers) or any((status_code(asset.status) != 'ready' for asset in plan.assets)) or len(plan.warnings) > 1


def brief_asset_summary(plan):
    parts = [msg('summary.assets', count=len(plan.assets))]
    if plan.plugins:
        collected = [p for p in plan.plugins if status_code(p.status) != 'external']
        ready = sum((status_code(p.status) == 'ready' for p in collected))
        if collected:
            parts.append(msg('summary.gizmos', ready=ready, total=len(collected)))
        external = len(plan.plugins) - len(collected)
        if external:
            parts.append(msg('summary.external', count=external))
        missing = sum((status_code(p.status) == 'not_found' for p in plan.plugins))
        if plan.automatic_plugins and missing:
            parts.append(msg('summary.skipped', count=missing))
    for status in (msg('status.missing'), msg('status.missing_frames'), msg('status.pending')):
        count = sum(status_code(asset.status) == status_code(status) for asset in plan.assets)
        if count:
            parts.append(msg('summary.status_count', status=status, count=count))
    if len(plan.warnings) > 1:
        parts.append(msg('summary.dependency_notes'))
    return join_messages(msg('common.list_separator'), parts)


class TransferMeter:
    def __init__(self):
        self.reset()

    def reset(self):
        self.started = time.monotonic()

    def describe(self, done, total):
        elapsed = time.monotonic() - self.started
        if elapsed < 0.25 or done <= 0:
            return msg('progress.speed_pending')
        rate = done / elapsed
        seconds = max(0, round((total - done) / rate))
        remaining = msg('time.seconds', seconds=seconds) if seconds < 60 else msg('time.minutes_seconds', minutes=seconds // 60, seconds=seconds % 60)
        return msg('progress.transfer_speed', speed=rate / 1048576, remaining=remaining)


class Worker(threading.Thread):
    def __init__(self, paths, output, events, session=None, language='zh-CN', before_run=None):
        super().__init__(daemon=False)
        self.language = language
        self.before_run = before_run
        self.paths = paths
        self.output = output
        self.events = events
        self.session = session or ScanSession()
        self.meter = TransferMeter()
        self.cancelled = threading.Event()
        self.last_progress = None
        self.last_progress_time = 0
        self.copy_sizes = []
        self.copied_files = 0
        self.copy_bytes = 0
        self.file_start_bytes = 0

    def emit(self, kind, *values):
        self.events.put((kind, values))

    def report_progress(self, phase, done, total, detail):
        now = time.monotonic()
        key = (phase, total)
        if key != self.last_progress or now - self.last_progress_time >= 0.05 or (total and done >= total):
            self.emit('progress', phase, done, total, detail)
            self.last_progress = key
            self.last_progress_time = now

    def file_copied(self, done, total, name):
        self.copied_files = done
        self.file_start_bytes = self.copy_bytes
        self.report_progress(msg('phase.copy'), done, total, name)
        if done == total:
            self.report_progress(msg('phase.prepare_verify'), 0, 0, msg('phase.write_projects'))

    def copy_byte_progress(self, done, total, name):
        self.copy_bytes = done
        fraction = 0
        if self.copied_files < len(self.copy_sizes):
            size = self.copy_sizes[self.copied_files]
            fraction = min(1, max(0, done - self.file_start_bytes) / size) if size else 0
        self.report_progress(msg('phase.copy'), self.copied_files + fraction, len(self.copy_sizes), fmt('{value0:.1f}/{value1:.1f} MiB · {value2} · {value3}', value0=done / 1048576, value1=total / 1048576, value2=self.meter.describe(done, total), value3=name))

    def run(self):
        completed = failed = 0
        cancelled = False
        try:
            if self.before_run: self.before_run()
            for index, path in enumerate(self.paths, 1):
                if self.cancelled.is_set():
                    cancelled = True
                    break
                self.emit('project', index, len(self.paths), path.name)
                self.emit('project_state', path, msg('status.scanning'))
                self.report_progress(msg('phase.read'), 0, 0, path.name)
                try:
                    plan = self.session.scan(path, self.cancelled.is_set, progress=lambda *values: self.report_progress(msg('phase.scan'), *values), reuse=bool(self.output), scanner=scan)
                    self.report_progress(msg('phase.measure'), 0, 0, path.name)
                    file_sizes, size = plan.measure(self.cancelled.is_set, progress=lambda *values: self.report_progress(msg('phase.measure'), *values))
                    if self.cancelled.is_set():
                        raise Cancelled()
                    self.emit('scanned', plan, size)
                    if self.output:
                        self.emit('project_state', path, msg('status.packaging'))
                        self.report_progress(msg('phase.prepare_pack'), 0, 0, path.name)
                        self.copy_sizes = list(file_sizes.values())
                        self.copied_files = self.copy_bytes = self.file_start_bytes = 0
                        self.meter.reset()
                        target = package(plan, self.output, self.cancelled.is_set, self.file_copied, byte_progress=self.copy_byte_progress, verify_progress=lambda *values: self.report_progress(msg('phase.verify'), *values), language=self.language)
                        self.emit('packaged', path, target)
                        self.emit('project_state', path, msg('status.packaged_review') if needs_review(plan) else msg('status.packaged'))
                    action = msg('log.packaged') if self.output else msg('log.scanned')
                    self.emit('message', fmt('{value0} · {value1} · {value2}', value0=action, value1=path.name, value2=brief_asset_summary(plan)), 'warning' if needs_review(plan) else 'success')
                    completed += 1
                except Cancelled:
                    cancelled = True
                    self.emit('project_state', path, msg('status.cancelled'))
                    detail = msg('log.incomplete_cleaned') if self.output else ''
                    self.emit('message', msg('log.cancelled', name=path.name, detail=detail), 'warning')
                    break
                except Exception as error:
                    failed += 1
                    self.emit('project_state', path, msg('status.blocked') if isinstance(error, DependencyError) else msg('status.failed'), fmt('{value0}: {value1}', value0=type(error).__name__, value1=error_message(error)))
                    self.emit('message', msg('log.failed', name=path.name, reason=readable_error(error)), 'error')
                self.emit('project_done', completed + failed, len(self.paths))
        finally:
            self.emit('finished', completed, failed, cancelled)
