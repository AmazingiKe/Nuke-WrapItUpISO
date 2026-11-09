"""Concise delivery summary derived from the completed package report."""
from i18n import status_code, status_message
from i18n import message as msg, fmt, join_messages, error_message
from collections import Counter
from html import escape
from app_version import APP_LABEL
from i18n import Translator


def cell(value):
    return escape(str(value)).replace('|', r'\|').replace('\r', ' ').replace('\n', ' ')


def filename(value):
    return str(value).replace('\\', '/').rstrip('/').rsplit('/', 1)[-1]


def asset_result(asset):
    if not asset.get('packed_path') or not asset.get('files', 0):
        return msg('result.failed')
    return msg('result.success') if status_code(asset.get('status')) == 'ready' else msg('result.partial')


def plugin_result(plugin):
    if status_code(plugin.get('status')) == 'external':
        return msg('result.external')
    return msg('result.success') if status_code(plugin.get('status')) == 'collected' and plugin.get('packed_path') else msg('result.failed')


def render_package_readme(report, language='zh-CN'):
    assets, plugins = report.get('assets', []), report.get('plugins', [])
    fonts = [asset for asset in assets if asset.get('kind') == 'font']
    media = [asset for asset in assets if asset.get('kind') != 'font']
    script = cell(filename(report['source_script']))
    counts = Counter([asset_result(a) for a in assets] + [plugin_result(p) for p in plugins])
    lines = [msg('readme.title'), '', msg('readme.generated_by', label=APP_LABEL), '', msg('detail.project', path=script), '',
             msg('readme.summary', success=counts[msg('result.success')], partial=counts[msg('result.partial')], failed=counts[msg('result.failed')]), '',
             msg('readme.media_title'), '']
    if media:
        lines += [msg('readme.media_table'), '| --- | --- | --- | ---: |']
        for asset in media:
            lines.append(fmt('| {value0} | {value1} | {value2} | {value3} |', value0=cell(asset.get('node', '')), value1=cell(filename(asset.get('original', ''))), value2=asset_result(asset), value3=asset.get('files', 0) if asset.get('packed_path') else 0))
    else:
        lines.append(msg('readme.no_assets'))
    lines += ['', msg('readme.fonts_title'), '']
    if fonts:
        lines += [msg('readme.fonts_table'), '| --- | --- | --- |']
        for asset in fonts:
            lines.append(fmt('| {value0} | {value1} | {value2} |', value0=cell(asset.get('node', '')), value1=cell(filename(asset.get('original', ''))), value2=asset_result(asset)))
    else:
        lines.append(msg('readme.no_fonts'))
    if report.get('font_warnings'):
        lines += ['', *report['font_warnings']]
    lines += ['', msg('readme.plugins_title'), '']
    if plugins:
        lines += [msg('readme.plugins_table'), '| --- | --- |']
        for plugin in plugins:
            lines.append(fmt('| {value0} | {value1} |', value0=cell(plugin.get('name', '')), value1=plugin_result(plugin)))
        if any((status_code(p.get('status')) == 'external' for p in plugins)):
            lines += ['', msg('readme.external_plugins')]
    else:
        lines.append(msg('readme.no_plugins'))
    lines += ['', msg('readme.open_title'), '']
    if any((status_code(p.get('status')) == 'collected' for p in plugins)):
        lines += [msg('readme.plugin_path'), '',
                  '```python', 'import nuke', msg('readme.plugin_example'), '```', '',
                  msg('readme.plugin_path_hint'), '']
    if report.get('relative_script') and report.get('relinked_script'):
        lines += [msg('readme.relative', filename=cell(report['relative_script'])), '',
                  msg('readme.absolute', filename=cell(report['relinked_script'])), '',
                  msg('readme.relink_scope'),
                  msg('readme.font_loading')]
    else:
        lines.append(msg('readme.open_project', filename=script))
    if report.get('original_path_script'):
        original = cell(report['original_path_script'])
        lines += ['', msg('readme.original', filename=original),
                  msg('readme.original_dependencies')]
    if report.get('script_dependency_warnings'):
        lines += ['', msg('readme.scripts')]
    lines += ['', msg('readme.move_folder'), '',
              msg('readme.report_hint'), '']
    translator = Translator(language)
    return '\n'.join(translator.render(line) for line in lines)
