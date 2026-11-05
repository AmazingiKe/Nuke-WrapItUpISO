"""Recognize a small subset of self-contained Nuke UI callbacks without execution."""
import ast
import re


def is_ui_callback(raw):
    """Accept only local node/knob queries, conditions and UI enable/visibility calls.

    This is deliberately not a general Python dependency resolver. Unknown syntax,
    imports, file/value mutations and dynamic calls retain the existing blocker.
    ``raw`` includes the original Tcl word delimiters so braced Python strings
    retain their escapes while quoted Nuke serialization is decoded once.
    """
    if raw.startswith('"') and raw.endswith('"'):
        escapes = {'n': '\n', 'r': '\r', 't': '\t', '\\': '\\', '"': '"',
                   '[': '[', ']': ']', '$': '$', '{': '{', '}': '}'}
        try:
            code = re.sub(r'\\(.)', lambda match: escapes[match[1]], raw[1:-1])
        except KeyError:
            return False
    elif raw.startswith('{') and raw.endswith('}'):
        code = raw[1:-1]
    else:
        return False
    try:
        tree = ast.parse(code.strip())
    except (SyntaxError, ValueError, RecursionError):
        return False

    def literal_name(value):
        return isinstance(value, ast.Constant) and isinstance(value.value, str)

    def expression(value, names):
        if isinstance(value, ast.Constant) and isinstance(value.value, (str, bool, int, float)):
            return 'value'
        if isinstance(value, ast.Name):
            return names.get(value.id)
        if isinstance(value, ast.Subscript):
            if expression(value.value, names) == 'node' and literal_name(value.slice):
                return 'knob'
        if isinstance(value, ast.Compare):
            if (all(isinstance(op, (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE)) for op in value.ops)
                    and all(expression(v, names) == 'value' for v in [value.left, *value.comparators])):
                return 'value'
        if isinstance(value, ast.BoolOp):
            if all(expression(v, names) == 'value' for v in value.values):
                return 'value'
        if isinstance(value, ast.UnaryOp) and isinstance(value.op, ast.Not):
            if expression(value.operand, names) == 'value':
                return 'value'
        if not isinstance(value, ast.Call) or value.keywords or not isinstance(value.func, ast.Attribute):
            return None
        owner = expression(value.func.value, names)
        method, args = value.func.attr, value.args
        if owner == 'nuke' and not args:
            return {'thisNode': 'node', 'thisKnob': 'knob'}.get(method)
        if owner == 'node' and method == 'knob' and len(args) == 1 and literal_name(args[0]):
            return 'knob'
        if owner == 'knob':
            if method in {'name', 'value'} and not args:
                return 'value'
            if method in {'setEnabled', 'setVisible'} and len(args) == 1 and expression(args[0], names) == 'value':
                return 'ui'
        return None

    def statements(body, names):
        for statement in body:
            if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
                target = statement.targets[0]
                kind = expression(statement.value, names)
                if not isinstance(target, ast.Name) or target.id == 'nuke' or kind not in {'node', 'knob', 'value'}:
                    return False
                names[target.id] = kind
            elif isinstance(statement, ast.If):
                if expression(statement.test, names) != 'value':
                    return False
                then_names, else_names = names.copy(), names.copy()
                if not statements(statement.body, then_names) or not statements(statement.orelse, else_names):
                    return False
                # Keep only aliases whose type is known on both control-flow paths.
                names.clear()
                names.update({key: kind for key, kind in then_names.items() if else_names.get(key) == kind})
            elif isinstance(statement, ast.Expr):
                if expression(statement.value, names) not in {'value', 'ui'}:
                    return False
            else:
                return False
        return True

    try:
        return statements(tree.body, {'nuke': 'nuke'})
    except RecursionError:
        return False
