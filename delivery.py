"""Render the absolute and portable project variants from the same copy plan."""

# FreeType scans this knob's literal value while loading Text2, before evaluating
# File_Knob expressions. Resolve just our generated fonts path before those nodes.
FONT_PATH_COMMAND = 'knob root.free_type_font_path [file join [file dirname [value root.name]] fonts]'


def media_reference_name(name):
    # Lowercase the filename while preserving Nuke's case-sensitive UDIM token.
    return '<UDIM>'.join(part.lower() for part in name.split('<UDIM>'))


def path_word(path, *, relative=False):
    # Escape literal path characters; only our own relocation expression executes.
    # Nuke unescapes once when reading .nk, then File_Knob evaluates again.
    value = str(path).replace('\\', '/').replace('$', r'\\\$').replace('[', r'\\\[').replace(']', r'\\]').replace('"', r'\"')
    prefix = '[file dirname [value root.name]]/' if relative else ''
    return '"' + prefix + value + '"'


def literal_word(path):
    value = str(path).replace('\\', '/').replace('$', r'\$').replace('[', r'\[').replace(']', r'\]').replace('"', r'\"')
    return '"' + value + '"'


def apply_edits(text, edits):
    for start, end, value in sorted(edits, reverse=True):
        text = text[:start] + value + text[end:]
    return text


def project_settings(text, target, *, relative, fonts):
    from core import knobs, node_blocks
    values = {'project_directory': '"[file dirname [value root.name]]"' if relative else path_word(target)}
    if fonts:
        values['free_type_font_path'] = path_word('fonts', relative=True) if relative else literal_word(target / 'fonts')
    edits = []
    for cls, pos, body in node_blocks(text):
        if cls != 'Root':
            continue
        existing = knobs(body, pos)
        # Let scriptOpen supply the actual filename before Text2 resolves fonts.
        # A serialized source name would point font lookup at the old project.
        if 'name' in existing:
            token = existing['name']
            line_start = text.rfind('\n', pos, token.start) + 1
            edits.append((line_start, token.end, ''))
        added = ''
        for name, value in values.items():
            if name in existing:
                token = existing[name]
                edits.append((token.start, token.end, value))
            else:
                added += f' {name} {value}\n'
        if added:
            edits.append((pos + len(body), pos + len(body), '\n' + added))
        if fonts and relative:
            edits.append((pos + len(body) + 1, pos + len(body) + 1, '\n' + FONT_PATH_COMMAND + '\n'))
        break
    else:
        # Generated/minimal scripts may omit Root entirely.
        if fonts:
            prefix = 'Root {\n' + ''.join(f' {k} {v}\n' for k, v in values.items()) + '}\n'
            if relative:
                prefix += FONT_PATH_COMMAND + '\n'
            edits.append((0, 0, prefix))
    return edits


def absolute_plugin_names(plugins):
    used = {(p.packed_name or p.name).casefold() for p in plugins}
    aliases = {}
    for plugin in plugins:
        if plugin.status != '就绪':
            continue
        base = (plugin.packed_name or plugin.name).lower() + '_relinked'
        name, number = base, 2
        while name.casefold() in used:
            name, number = f'{base}_{number}', number + 1
        used.add(name.casefold())
        aliases[plugin.name] = name
    return aliases
