"""Concise delivery summary derived from the completed package report."""
from collections import Counter
from html import escape


def cell(value):
    return escape(str(value)).replace('|', r'\|').replace('\r', ' ').replace('\n', ' ')


def filename(value):
    return str(value).replace('\\', '/').rstrip('/').rsplit('/', 1)[-1]


def asset_result(asset):
    if not asset.get('packed_path') or not asset.get('files', 0):
        return '未成功'
    return '成功' if asset.get('status') == '就绪' else '部分成功'


def plugin_result(plugin):
    return '成功' if plugin.get('status') == '已收集' and plugin.get('packed_path') else '未成功'


def render_package_readme(report):
    assets, plugins = report.get('assets', []), report.get('plugins', [])
    script = cell(filename(report['source_script']))
    counts = Counter([asset_result(a) for a in assets] + [plugin_result(p) for p in plugins])
    lines = ['# 打包结果', '', f'工程：{script}', '',
             f'成功 {counts["成功"]} 项 · 部分成功 {counts["部分成功"]} 项 · 未成功 {counts["未成功"]} 项', '',
             '## 素材', '']
    if assets:
        lines += ['| 节点 | 素材 / 序列 | 打包状态 | 已收集文件数 |', '| --- | --- | --- | ---: |']
        for asset in assets:
            lines.append(f'| {cell(asset.get("node", ""))} | {cell(filename(asset.get("original", "")))} | '
                         f'{asset_result(asset)} | {asset.get("files", 0) if asset.get("packed_path") else 0} |')
    else:
        lines.append('无素材。')
    lines += ['', '## 插件', '']
    if plugins:
        lines += ['| 插件 | 打包状态 |', '| --- | --- |']
        for plugin in plugins:
            lines.append(f'| {cell(plugin.get("name", ""))} | {plugin_result(plugin)} |')
    else:
        lines.append('无插件收集项。')
    lines += ['', '## 打开工程', '']
    if plugins:
        lines.append('双击 `打开工程.cmd`，或使用 Nuke 执行 `load_package.py`。')
    else:
        lines.append(f'使用 Nuke 打开 {script}。')
    if report.get('original_path_script'):
        original = cell(report['original_path_script'])
        lines += ['', f'原路径版本：{original}。保留原工程内容和原始路径，供对照或在原环境中使用。',
                  '此版本依赖原素材和插件环境；相对路径仍以原工程目录或原 project_directory 为基准。']
    lines += ['', '移动时请搬走整个文件夹。', '',
              '状态表示文件收集结果；详细原因、完整路径和校验信息见 `report.json`。', '']
    return '\n'.join(lines)
