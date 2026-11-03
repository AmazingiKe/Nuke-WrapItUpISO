import argparse
from pathlib import Path
from core import scan, package
from app_version import VERSION

def main():
    parser=argparse.ArgumentParser(description='Nuke 批量打包器，仅需 Python 标准库，无需 Nuke')
    parser.add_argument('--version', action='version', version=f'Nuke Batch Packager {VERSION}')
    parser.add_argument('scripts',nargs='+'); parser.add_argument('-o','--output'); parser.add_argument('--scan',action='store_true')
    parser.add_argument('--plugin-dir', action='append', help='插件搜索目录；可重复指定；指定后替代默认 NUKE_PATH 和 .nuke')
    parser.add_argument('--gizmo', action='append', default=[], metavar='NAME=FILE', help='指定同名 Gizmo 的实际来源')
    args=parser.parse_args()
    if not args.scan and not args.output: parser.error('打包需要 --output，或使用 --scan')
    overrides = {}
    for value in args.gizmo:
        name, separator, path = value.partition('=')
        if not separator or not name or not path: parser.error('--gizmo 格式应为 节点名=文件路径')
        overrides[name] = path
    failures=0
    for source in args.scripts:
        p=Path(source); paths=sorted(p.rglob('*.nk')) if p.is_dir() else [p]
        for path in paths:
            try:
                plan=scan(path, plugin_paths=args.plugin_dir, plugin_overrides=overrides); print(f'{path}: {len(plan.assets)} references, {plan.size/1048576:.1f} MiB')
                for a in plan.assets: print(f'  {a.status}: {a.node}.{a.knob} {a.raw} {a.note}')
                for w in plan.warnings: print('  Warning:',w)
                for plugin in plan.plugins: print(f'  {plugin.status}: {plugin.name} {plugin.source} {plugin.note}')
                if any(a.status!='就绪' for a in plan.assets) or plan.plugin_blockers: failures+=1
                if not args.scan: print('Saved:',package(plan,args.output))
            except Exception as e: failures+=1; print('Error:',path,e)
    return 1 if failures else 0
if __name__=='__main__': raise SystemExit(main())
