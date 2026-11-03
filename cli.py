import argparse
from pathlib import Path
from core import scan, package
from app_version import VERSION

def main():
    parser=argparse.ArgumentParser(description='Nuke 批量打包器，仅需 Python 标准库，无需 Nuke')
    parser.add_argument('--version', action='version', version=f'Nuke Batch Packager {VERSION}')
    parser.add_argument('scripts',nargs='+'); parser.add_argument('-o','--output'); parser.add_argument('--scan',action='store_true')
    args=parser.parse_args()
    if not args.scan and not args.output: parser.error('打包需要 --output，或使用 --scan')
    failures=0
    for source in args.scripts:
        p=Path(source); paths=sorted(p.rglob('*.nk')) if p.is_dir() else [p]
        for path in paths:
            try:
                plan=scan(path, automatic_plugins=True); print(f'{path}: {len(plan.assets)} references, {plan.size/1048576:.1f} MiB')
                for a in plan.assets: print(f'  {a.status}: {a.node}.{a.knob} {a.raw} {a.note}')
                for w in plan.warnings: print('  Warning:',w)
                for plugin in plan.plugins: print(f'  {plugin.status}: {plugin.name} {plugin.source} {plugin.note}')
                if any(a.status!='就绪' for a in plan.assets) or plan.plugin_blockers: failures+=1
                if not args.scan: print('Saved:',package(plan,args.output))
            except Exception as e: failures+=1; print('Error:',path,e)
    return 1 if failures else 0
if __name__=='__main__': raise SystemExit(main())
