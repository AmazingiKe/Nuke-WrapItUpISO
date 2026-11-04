"""Read common startup path declarations without importing or executing scripts."""
import ast
from dataclasses import dataclass, field
import os
from pathlib import Path
import re
import tokenize


@dataclass
class Discovery:
    paths: list = field(default_factory=list)
    errors: list = field(default_factory=list)


class Unknown(ValueError):
    pass


class StartupReader:
    def __init__(self, source, result, add, cancel):
        self.source, self.result, self.add, self.cancel = source, result, add, cancel
        self.values = {'__file__': str(source)}
        self.aliases = {'nuke': 'nuke'}
        self.steps = 0

    def name(self, node):
        if isinstance(node, ast.Name):
            return self.aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            return self.name(node.value) + '.' + node.attr
        return ''

    def path_call(self, node):
        return isinstance(node, ast.Call) and self.name(node.func) in ('nuke.pluginAddPath', 'nuke.pluginAppendPath')

    def report(self, node, reason):
        self.result.errors.append(f'插件路径需核对：{self.source}:{node.lineno} · {reason}')

    def value(self, node):
        if isinstance(node, ast.Constant) and isinstance(node.value, (str, bool, int, type(None))):
            return node.value
        if isinstance(node, ast.Name) and node.id in self.values:
            return self.values[node.id]
        if isinstance(node, (ast.List, ast.Tuple)):
            return [self.value(item) for item in node.elts]
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left, right = self.value(node.left), self.value(node.right)
            if isinstance(left, str) and isinstance(right, str):
                return left + right
        if isinstance(node, ast.JoinedStr):
            return ''.join(str(self.value(part.value)) if isinstance(part, ast.FormattedValue)
                           and part.format_spec is None and part.conversion == -1 else self.value(part)
                           for part in node.values)
        if isinstance(node, ast.Subscript) and self.name(node.value) == 'os.environ':
            key = self.value(node.slice)
            if isinstance(key, str) and key in os.environ:
                return os.environ[key]
            raise Unknown('环境变量未设置')
        if isinstance(node, ast.Call) and not node.keywords:
            name = self.name(node.func)
            args = [self.value(arg) for arg in node.args]
            if name in ('os.getenv', 'os.environ.get') and 1 <= len(args) <= 2 and isinstance(args[0], str):
                value = os.environ.get(*args)
                if value is None:
                    raise Unknown('环境变量未设置')
                return value
            if all(isinstance(arg, str) for arg in args):
                if name == 'os.path.join' and args:
                    return os.path.join(*args)
                if len(args) == 1:
                    functions = {'os.path.dirname': os.path.dirname, 'os.path.normpath': os.path.normpath,
                                 'os.path.expanduser': os.path.expanduser, 'os.path.expandvars': os.path.expandvars}
                    if name in functions:
                        return functions[name](args[0])
                    if name in ('os.path.abspath', 'os.path.realpath'):
                        return str((self.source.parent / args[0]).resolve())
        raise Unknown('动态表达式无法静态确定')

    def pending(self, node):
        for child in ast.walk(node):
            if self.path_call(child):
                self.report(child, '条件、循环或函数调用无法静态确定')

    def statements(self, statements, depth=0):
        from core import Cancelled
        if depth > 32:
            raise Unknown('启动配置嵌套过深')
        for node in statements:
            if self.cancel():
                raise Cancelled()
            self.steps += 1
            if self.steps > 10000:
                raise Unknown('启动配置超过静态解析步数限制')
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.aliases[alias.asname or alias.name.split('.')[0]] = alias.name if alias.asname else alias.name.split('.')[0]
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    self.aliases[alias.asname or alias.name] = f'{node.module}.{alias.name}'
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                try:
                    value = self.value(node.value)
                except (Unknown, TypeError, ValueError):
                    value = None
                for target in targets:
                    if isinstance(target, ast.Name):
                        self.values.pop(target.id, None)
                        self.aliases.pop(target.id, None)
                        if value is not None:
                            self.values[target.id] = value
            elif isinstance(node, ast.Expr) and self.path_call(node.value):
                call = node.value
                try:
                    argument = call.args[0] if call.args else next(k.value for k in call.keywords if k.arg == 'path')
                    path = self.value(argument)
                    if not isinstance(path, str) or not path.strip():
                        raise Unknown('路径为空或不是字符串')
                    path = os.path.expandvars(os.path.expanduser(path))
                    if re.search(r'\$\w+|\$\{[^}]+\}|%[^%]+%', path):
                        raise Unknown('路径中的环境变量未设置')
                    self.add((self.source.parent / path).resolve(),
                             front=self.name(call.func) == 'nuke.pluginAddPath')
                except (Unknown, ValueError, OSError, StopIteration) as error:
                    self.report(node, str(error) or '缺少路径参数')
            elif isinstance(node, ast.If):
                try:
                    condition = self.value(node.test)
                except (Unknown, ValueError, TypeError):
                    self.pending(node)
                    # Do not reuse assignments that may have changed in either branch.
                    self.invalidate(node)
                else:
                    self.statements(node.body if condition else node.orelse, depth + 1)
            elif isinstance(node, ast.For) and isinstance(node.target, ast.Name):
                try:
                    if any(isinstance(child, (ast.Break, ast.Continue, ast.Return, ast.Raise)) for child in ast.walk(node)):
                        raise Unknown('循环包含流程控制')
                    values = self.value(node.iter)
                    if not isinstance(values, list) or len(values) > 256:
                        raise Unknown('循环不是有限常量列表')
                except (Unknown, ValueError, TypeError):
                    self.pending(node)
                    self.invalidate(node)
                else:
                    for value in values:
                        self.values[node.target.id] = value
                        self.statements(node.body, depth + 1)
                    self.statements(node.orelse, depth + 1)
            else:
                self.pending(node)
                self.invalidate(node)

    def invalidate(self, node):
        for child in ast.walk(node):
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
                self.values.pop(child.id, None)
                self.aliases.pop(child.id, None)


def discover_paths(roots, cancel=lambda: False, progress=lambda a,b,c: None, *, observe=None):
    from core import Cancelled
    result = Discovery()
    seen, processed = set(), set()

    def register(path, before=None):
        path = Path(path).expanduser().resolve()
        if path in seen:
            return None
        if len(seen) >= 512:
            raise Unknown('自动发现目录超过 512 个')
        seen.add(path)
        if before is not None:
            result.paths.insert(result.paths.index(before), path)
        else:
            result.paths.append(path)
        return path

    for root in roots:
        register(root)

    def process(directory, depth=0):
        if cancel():
            raise Cancelled()
        if directory in processed: return
        processed.add(directory)
        if depth >= 128:
            result.errors.append(f'插件启动目录嵌套过深：{directory}')
            return
        source = directory / 'init.py'
        if observe: observe(source, content=True)
        if not source.is_file():
            return
        progress(0, 0, f'读取插件配置：{directory.name}')
        added = []
        def add(path, front=False):
            path = register(path, before=directory if front else None)
            if path is not None: added.append(path)
        try:
            if source.stat().st_size > 1024 * 1024:
                raise Unknown('配置文件超过 1 MiB')
            with tokenize.open(source) as stream:
                tree = ast.parse(stream.read(), filename=str(source))
            StartupReader(source, result, add, cancel).statements(tree.body)
        except (OSError, SyntaxError, UnicodeError, ValueError, RecursionError) as error:
            result.errors.append(f'插件配置无法解析：{source} · {error}')
        # Nuke executes each init completely, then visits its new paths in reverse.
        for path in reversed(added): process(path, depth + 1)

    # Initial search order is preserved; startup execution runs from low to high priority.
    for directory in reversed(result.paths.copy()): process(directory)
    result.errors = list(dict.fromkeys(result.errors))
    return result
