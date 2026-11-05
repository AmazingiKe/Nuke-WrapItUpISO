"""Conservative Tcl command inspection; never evaluates source expressions."""
import re


def inspect_commands(text):
    from core import node_blocks
    blockers, reviews = [], []
    blocks = list(node_blocks(text, class_tokens=True))
    # Knob bodies are saved data. Callbacks/expressions inside them are inspected
    # separately and must not become top-level execution blockers.
    chunks, end = [], 0
    for _, position, body, token in blocks:
        chunks.append(text[end:token.start])
        chunks.append('N' + re.sub(r'[^\r\n]', ' ', text[token.start + 1:token.end]))
        chunks.append(text[token.end:position - 1])
        chunks.append('{' + re.sub(r'[^\r\n]', ' ', body) + '}')
        end = position + len(body) + 1
    chunks.append(text[end:])
    source = ''.join(chunks)
    allowed = {'N'} | {
        'set', 'push', 'version', 'end_group', 'define_window_layout_xml', 'add_layer', 'stack'}

    def record(command, position):
        line = source.count('\n', 0, position) + 1
        name = command.removeprefix('::')
        if name in {'python', 'source', 'load', 'load_plugin'}:
            kind = '脚本执行命令' if name == 'python' else '外部脚本加载命令'
            blockers.append(f'第 {line} 行含{kind} {name}，需核对外部依赖')
        elif name.lower().endswith('.gizmo'):
            reviews.append(f'第 {line} 行直接引用外部 Gizmo 路径 {command}，尚未收集，需核对接收方路径')
        elif name not in allowed:
            reviews.append(f'第 {line} 行含 Tcl 命令 {command or "动态命令"}，执行及依赖未经验证')

    def walk(index=0, depth=0):
        if depth > 100:
            reviews.append('Tcl 命令嵌套过深，脚本依赖未经验证')
            return len(source)
        command = None
        quoted = False
        word_start = True
        while index < len(source):
            char = source[index]
            if char == '\\':
                if command is None:
                    record('', index)
                    command = ''
                index += 2
                continue
            if not quoted and char == ']' and depth:
                return index + 1
            if not quoted and char in '\n;':
                command, word_start = None, True
                index += 1
                continue
            if not quoted and char.isspace():
                word_start = True
                index += 1
                continue
            if not quoted and command is None and char == '#':
                newline = source.find('\n', index)
                index = len(source) if newline < 0 else newline
                continue
            if command is None:
                start = index
                while index < len(source) and source[index] not in ' \t\r\n;[]{}"\\':
                    index += 1
                command = source[start:index]
                record(command, start)
                if index > start:
                    word_start = False
                    continue
            if not quoted and char == '{' and word_start:
                braces = 1
                index += 1
                while index < len(source) and braces:
                    if source[index] == '\\':
                        index += 2
                        continue
                    if source[index] == '{': braces += 1
                    if source[index] == '}': braces -= 1
                    index += 1
                word_start = False
                continue
            if char == '"':
                quoted = not quoted
            elif char == '[':
                index = walk(index + 1, depth + 1)
                word_start = False
                continue
            word_start = False
            index += 1
        return index

    walk()
    return list(dict.fromkeys(blockers)), list(dict.fromkeys(reviews))
