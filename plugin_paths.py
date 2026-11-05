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


class LoopBreak(Exception):
    pass


class LoopContinue(Exception):
    pass


class StartupReader:
    def __init__(self, source, result, add, cancel, observe=None):
        self.source, self.result, self.add, self.cancel = source, result, add, cancel
        self.observe = observe
        # Nuke executes init.py in __main__ (verified with a generated fixture).
        self.values = {'__file__': str(source), '__name__': '__main__'}
        self.aliases = {'nuke': 'nuke'}
        self.functions = {}
        self.steps = 0

    def walk_directories(self, path):
        from core import Cancelled
        path = path if path.is_absolute() else self.source.parent / path
        if self.observe: self.observe(path)
        rows, entries = [], 0
        def failed(error):
            raise error
        for current, directories, files in os.walk(path, onerror=failed, followlinks=False):
            if self.cancel(): raise Cancelled()
            if self.observe: self.observe(Path(current))
            entries += len(directories) + len(files) + 1
            if entries > 20000: raise Unknown('递归目录项超过静态枚举限制')
            rows.append((current, directories, files))
        return rows

    def assign(self, target, value):
        if isinstance(target, ast.Name):
            self.values.pop(target.id, None)
            self.aliases[target.id] = ''
            self.functions.pop(target.id, None)
            if value is not None: self.values[target.id] = value
        elif isinstance(target, (ast.Tuple, ast.List)):
            if not isinstance(value, (tuple, list)) or len(target.elts) != len(value):
                for child in target.elts: self.assign(child, None)
                raise Unknown('解包赋值无法静态确定')
            for child, item in zip(target.elts, value): self.assign(child, item)
        else:
            raise Unknown('不支持的赋值目标')

    def call_function(self, call, depth):
        function = self.functions[call.func.id]
        parameters = function.args
        if (function.decorator_list or parameters.vararg or parameters.kwarg
                or parameters.kwonlyargs or parameters.posonlyargs or parameters.defaults):
            raise Unknown('辅助函数参数或装饰器无法静态确定')
        names = [arg.arg for arg in parameters.args]
        arguments = [self.value(arg) for arg in call.args]
        if len(arguments) > len(names): raise Unknown('辅助函数参数过多')
        bound = dict(zip(names, arguments))
        for keyword in call.keywords:
            if keyword.arg not in names or keyword.arg in bound:
                raise Unknown('辅助函数参数无法匹配')
            bound[keyword.arg] = self.value(keyword.value)
        if len(bound) != len(names): raise Unknown('辅助函数缺少参数')
        previous = self.values, self.aliases, self.functions
        self.values, self.aliases, self.functions = (mapping.copy() for mapping in previous)
        try:
            for name, value in bound.items():
                self.values[name] = value
                self.aliases[name] = ''
                self.functions.pop(name, None)
            self.statements(function.body, depth + 1)
        finally:
            self.values, self.aliases, self.functions = previous

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
        if isinstance(node, ast.Attribute):
            owner = self.value(node.value)
            if isinstance(owner, Path) and node.attr in {'parent', 'name', 'stem', 'suffix'}:
                return getattr(owner, node.attr)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            left, right = self.value(node.left), self.value(node.right)
            if isinstance(left, Path) and isinstance(right, (str, Path)):
                return left / right
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return not self.value(node.operand)
        if isinstance(node, ast.BoolOp):
            result = self.value(node.values[0])
            for item in node.values[1:]:
                if isinstance(node.op, ast.And) and not result: return result
                if isinstance(node.op, ast.Or) and result: return result
                result = self.value(item)
            return result
        if isinstance(node, ast.Compare):
            left = self.value(node.left)
            for op, right_node in zip(node.ops, node.comparators):
                right = self.value(right_node)
                if isinstance(op, ast.Eq): result = left == right
                elif isinstance(op, ast.NotEq): result = left != right
                elif isinstance(op, ast.In): result = left in right
                elif isinstance(op, ast.NotIn): result = left not in right
                else: raise Unknown('不支持的条件比较')
                if not result: return False
                left = right
            return True
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
        if isinstance(node, ast.Call):
            name = self.name(node.func)
            args = [self.value(arg) for arg in node.args]
            if name == 'pathlib.Path' and not node.keywords and len(args) == 1 and isinstance(args[0], (str, Path)):
                return Path(args[0])
            if name == 'str' and not node.keywords and len(args) == 1 and isinstance(args[0], (str, Path)):
                return str(args[0])
            if name == 'os.walk' and len(args) == 1 and not node.keywords and isinstance(args[0], (str, Path)):
                return self.walk_directories(Path(args[0]))
            if name == 'sorted' and len(args) == 1 and isinstance(args[0], list):
                if any(k.arg not in {'key', 'reverse'} for k in node.keywords):
                    raise Unknown('不支持的排序参数')
                keywords = {k.arg: k.value for k in node.keywords}
                reverse = self.value(keywords['reverse']) if 'reverse' in keywords else False
                key = keywords.get('key')
                if key is None: return sorted(args[0], reverse=bool(reverse))
                if (not isinstance(key, ast.Lambda) or len(key.args.args) != 1 or key.args.posonlyargs
                        or key.args.kwonlyargs or key.args.vararg or key.args.kwarg or key.args.defaults):
                    raise Unknown('排序键不是可静态解析的单参数表达式')
                def sort_key(item):
                    previous = self.values.copy()
                    try:
                        self.values[key.args.args[0].arg] = item
                        return self.value(key.body)
                    finally:
                        self.values = previous
                return sorted(args[0], key=sort_key, reverse=bool(reverse))
            if isinstance(node.func, ast.Attribute):
                try: owner = self.value(node.func.value)
                except Unknown: owner = None
                method = node.func.attr
                if isinstance(owner, Path):
                    absolute = owner if owner.is_absolute() else self.source.parent / owner
                    if method == 'resolve' and not args and all(k.arg == 'strict' for k in node.keywords):
                        strict = next((self.value(k.value) for k in node.keywords), False)
                        if self.observe: self.observe(absolute)
                        return absolute.resolve(strict=bool(strict))
                    if not node.keywords and not args:
                        if method == 'as_posix': return owner.as_posix()
                        if method in {'exists', 'is_dir', 'is_file'}:
                            if self.observe: self.observe(absolute)
                            return getattr(absolute, method)()
                        if method == 'iterdir':
                            from core import Cancelled
                            if self.observe: self.observe(absolute)
                            items = []
                            with os.scandir(absolute) as entries:
                                for entry in entries:
                                    if self.cancel(): raise Cancelled()
                                    items.append(Path(entry.path))
                                    if len(items) > 4096: raise Unknown('目录项超过静态枚举限制')
                            return items
                    if method == 'joinpath' and not node.keywords and all(isinstance(a, (str, Path)) for a in args):
                        return owner.joinpath(*args)
                    if method == 'rglob' and args == ['*'] and not node.keywords:
                        items = []
                        for current, directories, files in self.walk_directories(absolute):
                            # Only directory order matters for plugin registration;
                            # retain os.scandir's order within directory siblings.
                            items.extend(Path(current) / entry for entry in directories + files)
                        return items
                if isinstance(owner, str) and not node.keywords:
                    if method in {'lower', 'upper', 'casefold'} and not args:
                        return getattr(owner, method)()
                    if method in {'startswith', 'endswith'} and len(args) == 1 and isinstance(args[0], str):
                        return getattr(owner, method)(args[0])
            if node.keywords:
                raise Unknown('不支持的调用参数')
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
                                 'os.path.split': os.path.split,
                                 'os.path.expanduser': os.path.expanduser, 'os.path.expandvars': os.path.expandvars}
                    if name in functions:
                        return functions[name](args[0])
                    if name in ('os.path.abspath', 'os.path.realpath'):
                        return str((self.source.parent / args[0]).resolve())
        raise Unknown('动态表达式无法静态确定')

    def pending(self, node, visited=None):
        visited = set() if visited is None else visited
        for child in ast.walk(node):
            if self.path_call(child):
                self.report(child, '条件、循环或函数调用无法静态确定')
            elif (isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
                  and child.func.id in self.functions and child.func.id not in visited):
                visited.add(child.func.id)
                self.pending(self.functions[child.func.id], visited)

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
                except (Unknown, TypeError, ValueError, OSError):
                    value = None
                for target in targets:
                    try: self.assign(target, value)
                    except Unknown: self.invalidate(target)
            elif isinstance(node, ast.FunctionDef):
                self.functions[node.name] = node
                self.values.pop(node.name, None)
                self.aliases[node.name] = ''
            elif (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                  and isinstance(node.value.func, ast.Name) and node.value.func.id in self.functions):
                try:
                    self.call_function(node.value, depth)
                except (Unknown, ValueError, TypeError, OSError) as error:
                    self.report(node, str(error))
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
                except (Unknown, ValueError, TypeError, OSError):
                    self.pending(node)
                    # Do not reuse assignments that may have changed in either branch.
                    self.invalidate(node)
                    if any(isinstance(child, (ast.Break, ast.Continue, ast.Return, ast.Raise)) for child in ast.walk(node)):
                        raise Unknown('条件分支的流程控制无法确定')
                else:
                    self.statements(node.body if condition else node.orelse, depth + 1)
            elif isinstance(node, ast.For) and isinstance(node.target, (ast.Name, ast.Tuple, ast.List)):
                try:
                    values = self.value(node.iter)
                    if not isinstance(values, list) or len(values) > 20000:
                        raise Unknown('循环不是有限路径或常量列表')
                    for value in values:
                        self.assign(node.target, value)
                        try: self.statements(node.body, depth + 1)
                        except LoopContinue: continue
                        except LoopBreak: break
                    else:
                        self.statements(node.orelse, depth + 1)
                except (Unknown, ValueError, TypeError, OSError):
                    self.pending(node)
                    self.invalidate(node)
            elif isinstance(node, ast.Continue):
                raise LoopContinue()
            elif isinstance(node, ast.Break):
                raise LoopBreak()
            elif isinstance(node, (ast.Return, ast.Raise)):
                raise Unknown('启动配置包含无法静态执行的流程控制')
            else:
                self.pending(node)
                self.invalidate(node)

    def invalidate(self, node):
        for child in ast.walk(node):
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
                self.values.pop(child.id, None)
                self.aliases[child.id] = ''
                self.functions.pop(child.id, None)


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
            StartupReader(source, result, add, cancel, observe).statements(tree.body)
        except (OSError, SyntaxError, UnicodeError, ValueError, TypeError, RecursionError, LoopBreak, LoopContinue) as error:
            result.errors.append(f'插件配置无法解析：{source} · {error}')
        # Nuke executes each init completely, then visits its new paths in reverse.
        for path in reversed(added): process(path, depth + 1)

    # Initial search order is preserved; startup execution runs from low to high priority.
    for directory in reversed(result.paths.copy()): process(directory)
    result.errors = list(dict.fromkeys(result.errors))
    return result
