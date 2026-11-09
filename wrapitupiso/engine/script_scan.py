"""Conservative Tcl command inspection; never evaluates source expressions."""
from wrapitupiso.config.i18n import message as msg, fmt, join_messages, error_message
import re


def inspect_commands(text):
    from wrapitupiso.engine.core import node_blocks
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
            kind = msg('script.execution_command') if name == 'python' else msg('script.loading_command')
            blockers.append(msg('script.external_command', line=line, kind=kind, name=name))
        elif name.lower().endswith('.gizmo'):
            reviews.append(msg('script.explicit_gizmo', line=line, path=command))
        elif name not in allowed:
            reviews.append(msg('script.tcl_command', line=line, command=command or msg('script.dynamic_command')))

    def walk(index=0, depth=0):
        if depth > 100:
            reviews.append(msg('script.depth'))
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
