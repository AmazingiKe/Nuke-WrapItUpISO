# 开发工具与产物

所有命令在项目根目录执行。

| 路径 | 用途 |
| --- | --- |
| `WrapItUpISO.spec` | PyInstaller 单文件 EXE 配置 |
| `requirements-build.txt` | 构建及图标生成依赖 |
| `assets/prepare_app_icons.py` | 将提供的 Logo 转为应用图标和横版标识 |
| `assets/legacy_toolbar/` | 旧 Tk 工具栏图片及生成脚本，保留供追溯，不进入发布包 |
| `verify/` | Nuke 验证脚本与性能对比工具 |
| `build/` | 当前构建缓存，可重新生成 |
| `dist/` | 当前单文件 EXE 发布包 |
| `reports/` | 本地测试、覆盖率和独立运行验证结果，不进入 Git |
| `archive/` | 历史构建、旧版本及实验文件，不进入 Git |

```powershell
python -m pip install -r tools/requirements-build.txt
python tools/assets/prepare_app_icons.py
python -m PyInstaller --noconfirm --workpath tools/build --distpath tools/dist tools/WrapItUpISO.spec
```

EXE 名称和版本来自根目录 `version.json`。输出包含 Python 和 Qt6，发布前需在独立目录验证，不依赖源码或旁置运行环境。

性能工具兼容整理前后的目录结构。例如：

```powershell
python tools/verify/benchmark_pipeline.py --baseline-ref HEAD --result tools/reports/benchmark.json
```

语言切换基准使用原生 Qt 窗口，分别测量空窗口，以及 1,000 条依赖、5,000 行日志的情况；记录切换处理和后续绘制耗时，不操作真实工程：

```powershell
python tools/verify/benchmark_language.py --output tools/reports/benchmark-language.json
```

路径替换的 Nuke 实机验证会生成临时工程，检查手动指定、文件与文件夹混合匹配、Gizmo 内部素材与 Text2 字体替换；分别渲染绝对路径版和搬移后的相对路径版，并比较图像哈希：

```powershell
python tools/verify/verify_relink_nuke.py --nuke "C:/Program Files/Nuke15.1v5/Nuke15.1.exe" --output tools/reports/relink-nuke.json
```

更多验证命令见 [开发与验证](../docs/reference.md#开发与验证)。
