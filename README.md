# Nuke Batch Packager

> Fuck Vendor Nuke Pack. One Click, No Bullshit.

独立 Python 应用，使用 Tkinter / ttk 界面，直接读取 `.nk`，无需安装或启动 Nuke。

## Windows 启动

1. 双击 `tools/dist/NukeBatchPackager.exe`。这是单文件发布版，已内置 Python 运行时、Tcl/Tk 和拖放依赖，目标电脑无需安装 Python 或 Python Launcher。
2. 添加多个 `.nk` 或拖入文件夹；文件夹会递归查找 `.nk`。
3. 左侧列出已添加的工程和各自状态，底部显示已添加与已扫描的工程数量。点击“扫描素材”，再选择左侧工程，在右侧查看它的素材、缺失 / 待处理项和插件依赖提示。左右分栏宽度可拖动调整。
4. 将一个文件夹拖到“输出目录”输入框，自动使用该路径；也可点击 `...` 选择目录。点击“开始打包”。拖到其他区域的文件夹仍用于添加工程。

分发时只需复制 `NukeBatchPackager.exe`。程序启动时会将内置依赖解压到系统临时目录，启动可能需要几秒钟。

扫描和复制在后台线程执行，窗口保持可操作。整体进度条显示已处理工程数；当前阶段进度条显示节点扫描百分比或已复制数据量，大文件复制时也会更新。读取工程和生成报告期间显示等待动画。点击“取消”会清理当前未完成工程，保留已完成结果。

扫描和打包按钮处理左侧列表中的全部工程；选择某个工程仅切换右侧详情。右侧插件提示包含未识别节点和待核对的依赖，不代表已完整检测或收集所有插件。

右侧列表下方为“素材 / 插件状态”：采用与日志一致的字号、行距和状态颜色，优先显示结果与问题说明，详细路径放在后面。点击素材查看状态、原路径、解析路径和问题说明；点击列表空白处可返回工程与插件依赖提示。内容较多时可滚动查看。

日志仅显示本次操作的开始、各工程结果和最终汇总；绿色表示完成，棕黄色表示需处理，红色表示失败。详细路径、错误原因及插件提示在右侧状态区查看。

## 源码运行与构建 EXE

仅开发或自行构建时需要安装 Python 3.10 或更高版本，并保留 Tcl/Tk 支持。在项目根目录执行：

```powershell
python -m pip install -r requirements.txt
python app.py
```

打包配置与构建产物统一放在 `tools` 目录。生成单文件 EXE：

```powershell
python -m pip install -r tools/requirements-build.txt
python -m PyInstaller --workpath tools/build --distpath tools/dist tools/NukeBatchPackager.spec
```

生成的程序为 `tools/dist/NukeBatchPackager.exe`，无需附带 `_internal` 文件夹。
界面使用 Tkinter，文件拖入使用 `tkinterdnd2` 扩展；不依赖 Qt。

## 收集规则

- 保留原工程；每个工程生成“工程名 + 路径哈希”文件夹。
- 收集 `file`、`proxy`、`vfield_file`、`lut_file`、`cdl_file`、`ocio_config`、`font_file` 中的固定路径。
- 支持 `%04d`、`%d`、`####`、`<UDIM>`；复制目录中所有匹配文件，而非只收集时间线范围。
- 支持 Group 内的显式素材节点、空格及中文路径。
- 以源路径去重，同一工程中重复引用只复制一次；各工程独立打包，不跨工程共享。
- 不收集 Write / DeepWrite 的渲染输出；不改变其输出路径。
- 相对输入路径以固定的 Root `project_directory` 为基准，未设置则以 `.nk` 所在目录为基准。
- 重写为 `"[file dirname [value root.name]]/media/..."`，移动时请搬走整个打包文件夹。
- 缺失、动态路径保持原值，写入 `report.json`；有这些项时不能视为完整交付。
- 取消或复制失败会清理当前工程的未完成目录，已完成的其他工程保留；如果清理也被拒绝，将明确提示残留路径。
- 已有同名打包目录时跳过并报错，不覆盖旧结果。

## 检查与收集范围

不会执行工程中的 TCL、Python 或回调；不支持 `.nknc`、`.nkple`。
无法完整求值动态路径、帧动画路径和运行时生成的依赖；这类路径会标记待处理。
外部 Gizmo、Group 中未展开的 Gizmo 内容、二进制插件、系统字体和隐式 OCIO 配置/关联 LUT 不会自动打包。
自定义节点只有已支持的显式路径参数会被扫描，其他依赖需要手动核对。
未识别节点按类型合并提示，不表示缺失插件；仅凭 `.nk` 文本无法验证安装环境。
帧号检查依据 Read 的 first / last；未设置时只检查匹配序列的最小至最大帧。
素材去重依据文件路径，不比较内容。
默认保留原 Root 设置；搬移后的工程仍需在 Nuke 中核对色彩管理、插件与输出路径。

## 命令行（仅需 Python 标准库）

```bash
python cli.py "D:/shots/shot01.nk" "D:/shots/shot02.nk" --scan
python cli.py "D:/shots" --output "E:/delivery"
```

命令行遇到缺失 / 缺帧 / 待处理项或失败返回退出码 1。外部依赖警告仍须人工核对。

打包中存在 `PACKAGING_INCOMPLETE.txt`，仅在复制流程全部结束后删除；素材缺失及插件依赖仍需核对报告。

## 开发与验证

- `app.py`：Tkinter 界面；后台线程执行扫描和复制，通过队列交由主线程更新界面。
- `core.py`：工程解析、素材收集与路径重写。
- `cli.py`：命令行入口。
- `version.json`：版本号的唯一配置，窗口标题与 `python cli.py --version` 共用；修改后重新构建 EXE 即可同步。
- `tests/`：核心测试与 Tkinter 窗口流程测试，不参与程序运行。
- `tools/`：EXE 打包配置、构建依赖、构建缓存与输出。

在项目根目录执行：

```powershell
python -m unittest discover -s tests -v
```

窗口测试需要可用的桌面环境。23 项测试覆盖核心收集规则、窗口扫描与打包、简明日志与完整错误保留、素材状态详情、同名工程切换与依赖隔离、工程与输出目录拖入、异步进度刷新、大文件复制中途取消、错误恢复和关闭时等待清理。
尚未进行真实 Nuke 工程加载验证。首次使用建议先打包一个小工程，在 Nuke 中打开副本验证。
