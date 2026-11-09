"""In-memory scan reuse with conservative dependency invalidation."""
from wrapitupiso.config.i18n import message as msg, fmt, join_messages, error_message
import os
from pathlib import Path


class Snapshot:
    def __init__(self, cancel=lambda: False):
        self.cancel = cancel
        self.environment = dict(os.environ)
        self.records = {}
        self.reusable = True

    def fingerprint(self, path, content):
        from wrapitupiso.engine.core import Cancelled
        from wrapitupiso.engine.gizmo import digest_file
        if self.cancel():
            raise Cancelled()
        try:
            st = path.stat()
        except FileNotFoundError:
            return None
        value = (st.st_mode, st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_ino)
        # Oversized startup scripts are rejected by the parser; metadata suffices there.
        hash_content = content and path.is_file() and not (path.name == 'init.py' and st.st_size > 1024 * 1024)
        return (value, digest_file(path, self.cancel)) if hash_content else value

    def watch(self, path, content=False):
        path = Path(path)
        key = (path, content)
        if key not in self.records:
            try:
                self.records[key] = self.fingerprint(path, content)
            except OSError:
                self.reusable = False

    def valid(self, cancel=lambda: False):
        self.cancel = cancel
        if not self.reusable or self.environment != dict(os.environ):
            return False
        try:
            return all(self.fingerprint(path, content) == expected
                       for (path, content), expected in self.records.items())
        except OSError:
            return False


class ScanSession:
    """One window's scans; explicit scan refreshes plans, batches share the index."""
    def __init__(self):
        self.plans = {}
        self.index_entry = None

    def plugin_index(self, roots, cancel, progress):
        from wrapitupiso.engine.gizmo import index_plugins
        from wrapitupiso.engine.plugin_paths import discover_paths
        key = tuple(str(p) for p in roots)
        if self.index_entry:
            old_key, snapshot, result = self.index_entry
            if old_key == key and snapshot.valid(cancel):
                return (*result, snapshot)
        snapshot = Snapshot(cancel)
        discovered = discover_paths(roots, cancel, progress, observe=snapshot.watch)
        index, errors = index_plugins(discovered.paths, cancel, progress, observe=snapshot.watch)
        errors = errors + discovered.errors
        if errors:
            snapshot.reusable = False
        result = (discovered.paths, index, errors)
        self.index_entry = (key, snapshot, result)
        return (*result, snapshot)

    def scan(self, path, cancel=lambda: False, progress=lambda a,b,c: None, *, reuse=False, scanner=None):
        from wrapitupiso.engine import core
        from wrapitupiso.engine.gizmo import default_paths
        path = Path(path).resolve()
        previous = self.plans.get(path)
        roots = tuple(str(p) for p in default_paths())
        if reuse and previous and previous.snapshot and previous.cache_roots == roots:
            progress(0, 0, msg('phase.check_cache'))
            if previous.snapshot.valid(cancel) and (
                    previous.plugin_snapshot is None or previous.plugin_snapshot.valid(cancel)):
                return previous
        self.plans.pop(path, None)
        for attempt in range(2):
            roots = tuple(str(p) for p in default_paths())
            plan = (scanner or core.scan)(path, cancel, progress=progress,
                                         automatic_plugins=True, context=self)
            plan.cache_roots = roots
            if (plan.snapshot.valid(cancel)
                    and (plan.plugin_snapshot is None or plan.plugin_snapshot.valid(cancel))):
                self.plans[path] = plan
                return plan
            # Read errors remain visible in the plan, but are never cached.
            if not plan.snapshot.reusable or (plan.plugin_snapshot and not plan.plugin_snapshot.reusable):
                return plan
            progress(0, 0, msg('phase.rescan'))
        raise core.DependencyError(msg('scan.continuous_changes'))
