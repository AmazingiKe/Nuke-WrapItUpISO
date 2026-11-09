"""Concise delivery summary derived from the completed package report."""
from collections import Counter
from html import escape
from app_version import APP_LABEL


def cell(value):
    return escape(str(value)).replace('|', r'\|').replace('\r', ' ').replace('\n', ' ')


def filename(value):
    return str(value).replace('\\', '/').rstrip('/').rsplit('/', 1)[-1]


def asset_result(asset):
    if not asset.get('packed_path') or not asset.get('files', 0):
        return '未成功'
    return '成功' if asset.get('status') == '就绪' else '部分成功'


def plugin_result(plugin):
    if plugin.get('status') == '外部依赖':
        return '不打包（接收方安装）'
    return '成功' if plugin.get('status') == '已收集' and plugin.get('packed_path') else '未成功'


def render_package_readme(report):
    assets, plugins = report.get('assets', []), report.get('plugins', [])
    fonts = [asset for asset in assets if asset.get('kind') == 'font']
    media = [asset for asset in assets if asset.get('kind') != 'font']
    script = cell(filename(report['source_script']))
    counts = Counter([asset_result(a) for a in assets] + [plugin_result(p) for p in plugins])
    lines = ['# 打包结果', '', f'生成工具：{APP_LABEL}', '', f'工程：{script}', '',
             f'成功 {counts["成功"]} 项 · 部分成功 {counts["部分成功"]} 项 · 未成功 {counts["未成功"]} 项', '',
             '## 素材', '']
    if media:
        lines += ['| 节点 | 素材 / 序列 | 打包状态 | 已收集文件数 |', '| --- | --- | --- | ---: |']
        for asset in media:
            lines.append(f'| {cell(asset.get("node", ""))} | {cell(filename(asset.get("original", "")))} | '
                         f'{asset_result(asset)} | {asset.get("files", 0) if asset.get("packed_path") else 0} |')
    else:
        lines.append('无素材。')
    lines += ['', '## 字体（fonts）', '']
    if fonts:
        lines += ['| 节点 | 字体 | 打包状态 |', '| --- | --- | --- |']
        for asset in fonts:
            lines.append(f'| {cell(asset.get("node", ""))} | {cell(filename(asset.get("original", "")))} | {asset_result(asset)} |')
    else:
        lines.append('无字体收集项。')
    if report.get('font_warnings'):
        lines += ['', *report['font_warnings']]
    lines += ['', '## 插件', '']
    if plugins:
        lines += ['| 插件 | 打包状态 |', '| --- | --- |']
        for plugin in plugins:
            lines.append(f'| {cell(plugin.get("name", ""))} | {plugin_result(plugin)} |')
        if any(p.get('status') == '外部依赖' for p in plugins):
            lines += ['', '外部插件不随包提供，接收方需安装兼容版本并配置授权。']
    else:
        lines.append('无插件收集项。')
    lines += ['', '## 打开工程', '']
    if any(p.get('status') == '已收集' for p in plugins):
        lines += ['打开工程前，在 Nuke 的 Script Editor（Python）中将本包 `gizmos` 文件夹加入搜索路径：', '',
                  '```python', 'import nuke', 'nuke.pluginAddPath(r"D:/交付包实际路径/gizmos")', '```', '',
                  '将示例路径替换为当前交付包的位置。移动交付包后需重新指定；也可由接收方统一配置插件搜索路径。', '']
    if report.get('relative_script') and report.get('relinked_script'):
        lines += [f'推荐交付：{cell(report["relative_script"])}。素材与字体使用相对路径，移动整个文件夹后仍可读取。', '',
                  f'固定位置使用：{cell(report["relinked_script"])}。素材与字体使用打包位置的绝对路径，移动后需改用 relative 版或重新打包。', '',
                  '两份工程仅重链接成功收集的文件；未收集项保留原引用，请核对表格和报告。',
                  'Text2 的字体文件在 fonts 中，通过工程字体搜索目录加载；无需安装到系统。']
    else:
        lines.append(f'使用已配置好插件环境的 Nuke 打开 {script}。')
    if report.get('original_path_script'):
        original = cell(report['original_path_script'])
        lines += ['', f'原路径版本：{original}。保留原工程内容和原始路径，供对照或在原环境中使用。',
                  '此版本依赖原素材和插件环境；相对路径仍以原工程目录或原 project_directory 为基准。']
    if report.get('script_dependency_warnings'):
        lines += ['', '按钮与回调脚本已原样保留，脚本依赖未经验证；核对清单见 `report.json`。']
    lines += ['', '移动时请搬走整个文件夹。', '',
              '状态表示文件收集结果；详细原因、完整路径和校验信息见 `report.json`。', '']
    return '\n'.join(lines)
