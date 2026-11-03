# Nuke Batch Packager

> Fuck Vendor Nuke Pack. One Click, No Bullshit.

独立 Python 应用，使用 Tkinter / ttk 界面，直接读取 `.nk`，无需安装或启动 Nuke。

## Windows 启动

1. 双击 `tools/dist/NukeBatchPackager.exe`。这是单文件发布版，已内置 Python 运行时、Tcl/Tk 和拖放依赖，目标电脑无需安装 Python 或 Python Launcher。
2. 点击左侧工程列表上方的 `+`，打开 Windows 原生文件选择器。支持 Ctrl / Shift 多选文件夹、`.nk` 工程，也可混选，点击“添加选中项”加入列表。双击文件夹进入目录；仅选择 `.nk` 时也可点击“打开”。文件夹会递归查找 `.nk`，没有工程时在日志提示，重复工程不会重复添加。仍支持直接拖入文件或文件夹。
3. 点击工具栏的放大镜图标“扫描素材与插件”。左侧选择工程，右侧分别列出素材和 Gizmo / 插件条目，点击条目查看来源与问题。左右分栏宽度可拖动调整。
4. 将一个文件夹拖到“输出目录”输入框，自动使用该路径；也可点击 `...` 选择目录。点击三角形图标“开始打包”。拖到其他区域的文件夹仍用于添加工程。

输出目录下方统一使用图标工具栏：加号添加、垃圾桶清空、放大镜扫描、三角形开始打包，右侧方块取消任务。鼠标悬停或键盘 Tab 聚焦时显示中文提示。

分发时只需复制 `NukeBatchPackager.exe`。程序启动时会将内置依赖解压到系统临时目录，启动可能需要几秒钟。

扫描和复制在后台线程执行，窗口保持可操作。合并进度条按工程数划分区间：深色代表已处理工程，浅色代表当前工程在当前阶段的任务进度。下方显示任务数、百分比和正在处理的文件；复制阶段按文件数计任务，大文件内部仍按复制量平滑更新。任务数量尚未确定时，在当前工程区间内显示活动动画。点击“取消”会清理当前未完成工程，保留已完成结果。

扫描和打包按钮处理左侧列表中的全部工程；选择某个工程仅切换右侧详情。默认自动搜索插件、选择同名候选，缺失插件记录后跳过，其余内容继续打包，无需逐个指定来源。无法读取或解析的 Gizmo、脚本依赖及校验失败仍会阻止该工程生成交付包。

右侧列表依次显示“节点、类型、原路径、文件数、状态”。类型为“素材”或“插件”，最右侧状态仅用 `√` 表示就绪、`×` 表示存在待处理问题，不再显示“说明”列。选中条目后，可在下方“素材 / 插件状态”查看具体原因、参数和路径；点击列表空白处返回工程概况。详情采用与日志一致的字号、行距和状态颜色，可滚动查看。

日志仅显示本次操作的开始、各工程结果和最终汇总；绿色表示完成，棕黄色表示需处理，红色表示失败。详细路径、错误原因及插件提示在右侧状态区查看。

## Gizmo 扫描与交付

- 默认从 `NUKE_PATH` 和用户 `.nuke` 开始，静态读取 `init.py` 中的 `nuke.pluginAddPath` / `pluginAppendPath`，继续读取新增目录的 `init.py`。支持相对路径、常量变量、环境变量、`os.path.join`、`dirname(__file__)`、字符串拼接和常量列表循环。不会运行配置脚本、导入外部模块或执行工程回调；依赖 Nuke 版本、运行时条件或自定义函数的路径无法自动确定时，会写入状态与报告。菜单脚本里的路径和其他模块动态注册的路径暂不解析。
- 插件目录自动发现，界面不再提供手动指定目录或来源的入口。自动发现的目录同样会递归查找 Gizmo，循环注册及重复目录会去重。
- 按工程引用查找 `.gizmo`，递归扫描其中的其他 Gizmo 和显式素材；收集原生文件参数、自定义 File 参数及指向已知文件参数的 Link 参数。相对素材仍按工程的 `project_directory` 解析，不猜测为插件所在目录。
- 同名文件内容不同时，按程序推导的搜索顺序自动选择第一个候选，并在报告中保留来源、候选列表和选择提示。`pluginAddPath` 的目录优先，`pluginAppendPath` 的目录后置；这不是从 Nuke 运行进程获取的实际加载来源。相同内容的多个副本可自动去重。
- 缺失 Gizmo 保留原引用并跳过收集，日志显示缺失数量，报告标记“未收集”和 `gizmo_dependencies_complete: false`。父 Gizmo 已找到但子 Gizmo 缺失时，仍收集父文件。循环依赖、定义不完整、内部素材缺失 / 缺帧 / 动态路径、脚本回调或 Python/Tcl 按钮依赖无法确定时，仍阻止打包并列明原因。二进制插件也保持待核对，不自动打包授权或关联库。
- Gizmo 写入交付包的 `gizmos/`，内部素材写入 `media/` 并改为相对工程路径。`report.json` 列出来源、原文件哈希、引用关系、输出文件哈希。扫描后工程或 Gizmo 变化会拒绝打包；复制完成后重新读取交付文件校验。

**包含 Gizmo 的包请用 `打开工程.cmd` 打开，不要直接双击 `.nk`。** 加载入口会先校验包内文件，再注册本包 Gizmo 并打开工程。整个目录可移动，支持中文和空格路径；接收方需要安装相应版本的 Nuke，但不需要另装 Python。

启动入口优先找与工程匹配且唯一的 Nuke 安装；不能唯一确定时弹出 EXE 选择框。也可把 Nuke EXE 路径作为参数传给 `打开工程.cmd`，或设置 `NUKE_EXECUTABLE`。启动采用 Nuke `--safe`，避免机器原有插件覆盖本包来源；依赖原有工作室启动脚本的工程需要另行核对。批处理 / 其他系统可在交付目录执行 `Nuke --safe -ti load_package.py`，渲染农场需自行将本包 `gizmos` 加入搜索路径。

“就绪 / 已收集”表示静态扫描和文件校验通过，**不表示所有 Nuke 版本均能加载、授权齐全或渲染结果一致**。报告始终单列 `nuke_runtime_verified: false`；程序不会启动 Nuke 自动渲染生产工程。Nuke 的插件搜索与启动机制见 [Foundry 文档](https://learn.foundry.com/nuke/15.1/content/comp_environment/configuring_nuke/sourcing_gizmos.html)。

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
外部 Gizmo 及其嵌套 Gizmo 按上面的规则收集；二进制插件、系统字体、隐式 OCIO 配置/关联 LUT 和任意脚本动态生成的依赖不自动收集。
内置节点名单用于排除标准节点；未列入名单且找不到对应文件的节点会显示“未找到”，也可能是较新版本内置节点，不能直接断言缺少第三方插件。
帧号检查依据 Read 的 first / last；未设置时只检查匹配序列的最小至最大帧。
素材去重依据文件路径，不比较内容。
默认保留原 Root 设置；搬移后的工程仍需在 Nuke 中核对色彩管理、插件与输出路径。

## 命令行（仅需 Python 标准库）

```bash
python cli.py "D:/shots/shot01.nk" "D:/shots/shot02.nk" --scan
python cli.py "D:/shots" --output "E:/delivery"
python cli.py "D:/shots/shot01.nk" --scan
python cli.py "D:/shots/shot01.nk" --output "E:/delivery"
```

命令行与界面使用相同的自动插件搜索和选择流程，不提供手动指定插件目录或来源的参数。缺失 Gizmo 记入报告并跳过；素材缺失 / 缺帧 / 待处理、阻止打包的插件问题或执行失败返回退出码 1。

打包中存在 `PACKAGING_INCOMPLETE.txt`，仅在复制流程全部结束后删除；素材缺失及插件依赖仍需核对报告。

## 开发与验证

- `app.py`：Tkinter 界面；后台线程执行扫描和复制，通过队列交由主线程更新界面。
- `project_picker.py`：Windows 原生文件选择器，支持文件夹与 `.nk` 混合多选。
- `plugin_paths.py`：静态解析启动配置，自动发现插件目录，不执行原配置代码。
- `core.py`：工程解析、素材收集与路径重写。
- `gizmo.py`：插件索引、依赖递归、冲突检查、交付文件校验及 Nuke 加载入口。
- `cli.py`：命令行入口。
- `version.json`：版本号的唯一配置，窗口标题与 `python cli.py --version` 共用；修改后重新构建 EXE 即可同步。
- `tests/`：核心测试与 Tkinter 窗口流程测试，不参与程序运行。
- `tools/`：EXE 打包配置、构建依赖、构建缓存与输出。

在项目根目录执行：

```powershell
python -m unittest discover -s tests -v
```

窗口测试需要可用的桌面环境。测试覆盖素材收集、嵌套 Gizmo、内部素材、自定义文件参数、同名冲突与来源指定、循环与缺失依赖、源文件变化、校验失败清理、命令行退出码，以及窗口扫描 / 打包 / 取消与错误恢复。

已使用 Nuke 15.1v5 验证生成的嵌套 Gizmo 测试工程：移除原插件与素材，移动交付目录到中文路径，分别用终端和 GUI 加载入口打开并校验像素值。可复现的验证命令（需要本机 Nuke 和有效许可，仅运行生成的测试工程）：

```powershell
python tools/verify_gizmo_nuke.py --nuke "C:/Program Files/Nuke15.1v5/Nuke15.1.exe"
python tools/verify_gizmo_nuke.py --nuke "C:/Program Files/Nuke15.1v5/Nuke15.1.exe" --gui
```

这不替代对实际生产工程的 Nuke 加载和渲染验收。
