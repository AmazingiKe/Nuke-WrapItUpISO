# WrapItUpISO 详细使用与技术说明

[返回项目首页](../README.md)

独立 Python 应用，使用 Qt6 / PySide6 深色界面，直接读取 `.nk`，无需安装或启动 Nuke。

## Windows 启动

1. 双击 `tools/dist/WrapItUpISO 1.1.32.exe`。这是单文件发布版，已内置 Python 运行时、Qt6 和全部界面依赖，目标电脑无需安装 Python 或 Python Launcher。
2. 点击左侧工程列表上方的 `+`，打开 Windows 原生文件选择器。支持 Ctrl / Shift 多选文件夹、`.nk` 工程，也可混选，点击“添加选中项”加入列表。双击文件夹进入目录；仅选择 `.nk` 时也可点击“打开”。文件夹会递归查找 `.nk`，没有工程时在日志提示，重复工程不会重复添加。仍支持直接拖入文件或文件夹。
3. 点击工具栏的放大镜图标“扫描素材与插件”。左侧选择工程，右侧分别列出素材和 Gizmo / 插件条目，点击条目查看来源与问题。左右分栏宽度可拖动调整。
4. 将一个文件夹拖到“输出目录”输入框，自动使用该路径；也可点击文件夹图标选择目录。点击三角形图标“开始打包”。拖到其他区域的文件夹仍用于添加工程。

界面采用 Nuke 风格的深灰面板、橙色高亮和紧凑工具栏。顶部为输出目录、扫描依赖、开始打包和取消；左侧“工程”面板的加号用于添加，垃圾桶用于清空。右侧为素材与插件列表，下方分别为日志和属性。左右及上下分栏都可拖动调整，“视图 → 重置面板布局”可恢复默认布局。鼠标悬停可查看按钮说明。

快捷键：`Ctrl+O` 添加工程 / 文件夹，`Ctrl+R` 扫描依赖，`Ctrl+Enter` 开始打包；工程列表中的 `Delete` 移除选中项，依赖列表中的 `Ctrl+C` 复制路径。任务执行期间禁用修改操作，关闭窗口会等待取消和清理完成。

添加文件夹在后台查找，工程分批显示，支持取消；取消后保留已经加入的工程。Ctrl / Shift 多选工程后按 Delete 或右键“移除选中工程”，只移除列表记录，不删除源文件。右上角“只看 ×”筛选未就绪的素材和插件；选中条目按 Ctrl+C 或右键复制解析路径。打包成功后，选中对应工程，点击日志旁的“打开输出”或“README”查看结果。

重新扫描、取消或再次打包失败时，仍可打开该工程上一次成功打包的目录和 README；下一次成功打包后入口更新到新目录。移除工程或清空列表时清除对应记录。

分发时只需复制 `WrapItUpISO 1.1.32.exe`。程序启动时会将内置依赖解压到系统临时目录，启动可能需要几秒钟。

扫描和复制在后台线程执行，窗口保持可操作。连续双色进度条中，深色代表已处理工程，浅色紧接深色，代表当前工程在当前阶段的任务进度；不绘制工程分隔线，进度变化采用短暂平滑过渡。上方保留工程数量，下方显示任务数、百分比和正在处理的文件；复制阶段按文件数计任务，大文件内部仍按复制量平滑更新。任务数量尚未确定时，浅色部分贴着深色边缘缓慢伸缩。点击“取消”会清理当前未完成工程，保留已完成结果。

复制时显示平均速度与本工程复制阶段的预计剩余时间（不含后续校验时间）。复制结束后切换为“校验文件”，逐个验证交付文件；大文件校验也会更新进度。通过校验并写完报告后才标记打包完成。

同一批次复用插件目录索引；一次工程及其 Gizmo 的扫描中，共享素材目录列表与序列匹配结果，同一目录不会因多个节点引用而重复枚举。扫描后直接打包时，检查工程、Gizmo 候选、插件启动配置、环境变量、素材文件和目录的变化；未变化时复用扫描结果，变化时自动重新扫描。此缓存只保存在本次程序运行的内存中；再次点击放大镜会强制重新扫描。读取失败的记录不会复用。缓存不省略复制时的源文件变更检查或复制后的完整 SHA-256 校验；已验证的目标哈希直接用于报告，避免再读取一遍全部素材。枚举目录、统计大小和准备复制时逐项检查取消请求；正在等待操作系统返回的单次文件访问结束后才能响应取消。

扫描和打包按钮处理左侧列表中的全部工程；选择某个工程仅切换右侧详情。默认自动搜索插件、选择同名候选，缺失插件记录后跳过，其余内容继续打包，无需逐个指定来源。按钮与回调脚本原样保留，无法静态确认的脚本依赖只提示核对，不阻止默认打包。无法读取或解析的 Gizmo、顶层脚本执行 / 加载命令及校验失败仍会阻止该工程生成交付包。

右侧列表依次显示“节点、类型、原路径、文件数、状态”。类型为“素材”、“字体”或“插件”，最右侧状态用 `√` 表示就绪、`×` 表示存在待处理问题、`—` 表示不打包的外部插件，不再显示“说明”列。选中条目后，可在下方“素材 / 插件状态”查看具体原因、参数和路径；点击列表空白处返回工程概况。详情采用与日志一致的字号、行距和状态颜色，可滚动查看。

日志与素材 / 插件状态在窗口底部左右并排，分栏宽度可拖动调整，上方素材列表使用完整高度。日志仅显示本次操作的开始、各工程结果和最终汇总；绿色表示完成，棕黄色表示需处理，红色表示失败。详细路径、错误原因及插件提示在底部右侧状态区查看。

## Gizmo 扫描与交付

- 默认按用户 `.nuke`、`NUKE_PATH` 中的目录顺序搜索，静态读取 `init.py` 中的 `nuke.pluginAddPath` / `pluginAppendPath`，继续读取新增目录的 `init.py`。支持相对路径、常量变量、环境变量、`os.path.join`、`dirname(__file__)`、字符串拼接和常量列表循环；也支持受限的 `Path(__file__)`、路径拼接、`exists` / `is_dir`、`iterdir` / `rglob("*")`、`os.walk`、按名称排序、可确定的条件与循环控制，以及同文件内用于注册目录的简单辅助函数。目录枚举和函数展开有数量、步数及深度限制，检查到的目录变化会使扫描缓存失效。不会运行配置脚本、导入外部模块或执行工程回调；依赖 Nuke 版本、运行时条件、外部模块或不支持的函数写法而无法自动确定的路径，会写入状态与报告。菜单脚本里的路径和其他模块动态注册的路径暂不解析。
- 插件目录自动发现，界面不提供手动指定目录或来源的入口。只索引已发现的注册目录本层文件；子目录由启动配置注册后继续处理，未注册的备份子目录不会作为插件候选，循环注册及重复目录会去重。
- 按工程引用查找 `.gizmo`，递归扫描其中的其他 Gizmo 和显式素材；收集原生文件参数、自定义 File 参数及指向已知文件参数的 Link 参数。内嵌 Group 和其他节点自身声明的 File 参数也按各节点独立收集，同名字符串参数不会被误收。相对素材仍按工程的 `project_directory` 解析，不猜测为插件所在目录。
- 工程或嵌套 Gizmo 直接以绝对路径引用 `.gizmo` 时，优先收集指定文件，将交付副本中的引用改为包内唯一名称；支持路径中的空格和中文，不同目录的同名文件分别保存。指定文件缺失时保留原引用并报告，不使用搜索目录中的同名文件替代；相对路径和动态 Gizmo 引用仍需核对。源工程及原路径备份保持不变，报告记录原引用和包内名称。
- 同名文件内容不同时，按程序推导的搜索顺序自动选择第一个候选，并在报告中保留来源、候选列表和选择提示。`pluginAddPath` 插到当前配置所属目录之前，同一配置中的声明保持先后顺序；`pluginAppendPath` 追加到搜索列表末尾。初始目录及各配置新增目录的初始化按反向顺序处理。这些规则已与 Nuke 15.1v5 对照验证，程序仍不读取 Nuke 运行进程中的实际加载来源。相同内容的多个副本可自动去重。
- RSMB 等具有 `OFX` 厂商标识的节点统一标记为“外部依赖”：不查找或复制对应插件文件，不阻止工程或父 Gizmo 打包，接收方自行安装兼容版本并配置授权。界面不计为缺失插件，交付 README 标记“不打包（接收方安装）”，报告保留名称与引用位置；不会宣称插件依赖完整。非 OFX 的 `.dll` / `.so` / `.dylib` 也作为外部依赖处理，不复制且不阻止打包。最高优先级目录同时包含 Gizmo 与二进制候选时保留外部依赖并提示核对；较低优先级的二进制候选不会阻止收集较高优先级 Gizmo。
- 缺失 Gizmo 保留原引用并跳过收集，日志显示缺失数量，报告标记“未收集”和 `gizmo_dependencies_complete: false`。父 Gizmo 已找到但子 Gizmo 缺失时，仍收集父文件。循环依赖、定义不完整、内部素材缺失 / 缺帧 / 动态路径仍阻止打包并列明原因。二进制插件及其授权和关联库由接收方安装。
- 所有工程都会检查显式回调、Python/Tcl 按钮和外部脚本加载命令，包括没有 Gizmo 的工程。静态分析可识别仅通过 `nuke.thisNode()` / `thisKnob()` 查询当前节点参数、按条件调用 `setEnabled` / `setVisible` 的界面回调（例如 Glow 的 crop / overscan 联动），保留原代码并允许打包，不会执行回调。默认自动模式不把“无法静态确认脚本依赖”等同于缺失：其余回调和按钮（包括帮助链接、随机种子、内部节点生成，以及可能引用外部模块的代码）原样保留并列为核对项，提示含工程 / Gizmo、节点和按钮名称，不执行代码、不自动收集其动态依赖。报告单列 `script_dependency_warnings`；存在核对项时 `gizmo_dependencies_complete` 为 `false`。`script_dependencies_verified` 始终为 `false`，并注明仅完成静态扫描，不再将“未发现问题”当成脚本运行环境已验证。这只表示文件已收集，不保证脚本在接收方环境中可运行。开发接口 `scan(..., automatic_plugins=False)` 保留严格拦截；直接执行的 `source` / `load` / `load_plugin` / `python` 命令，以及方括号替换或分号后出现的这些命令，仍需解决后才能打包；无法静态确定的 Tcl 控制结构和节点脚本表达式列入核对清单。
- Gizmo 写入交付包的 `gizmos/`，为绝对路径版和相对路径版生成不同类名的副本，内部素材和字体分别重链接；两份副本共用 `media/` 与 `fonts/` 中的文件。`report.json` 列出两种类名、来源、原文件哈希、引用关系、输出文件哈希。界面打包前发现工程或 Gizmo 变化会重新扫描；打包过程中来源变化或校验失败会终止当前工程并清理未完成目录。复制完成后重新读取交付文件校验。

交付包不生成 `.bat`、`.cmd`、PowerShell 或 Python 启动器，也不自动启动 Nuke。使用接收方正常的 Nuke 环境打开打包后的 `.nk`；OFX、二进制插件和其他未收集依赖需由接收方安装或配置。

如果包内包含已收集的 Gizmo，打开工程前先在 Nuke 的 Script Editor（Python）中注册本包目录，或由工作室统一配置搜索路径：

```python
import nuke
nuke.pluginAddPath(r"D:/交付包实际路径/gizmos")
```

将示例路径替换为当前交付包的位置。移动交付包后需要重新注册路径，并核对接收方的同名插件版本。打包过程中仍执行完整文件校验，报告保留 SHA-256；打开工程时不再附带自动校验或启动行为。

“就绪 / 已收集”表示静态扫描和文件校验通过，**不表示所有 Nuke 版本均能加载、授权齐全或渲染结果一致**。报告始终单列 `nuke_runtime_verified: false`；程序不会启动 Nuke 自动渲染生产工程。Nuke 的插件搜索与启动机制见 [Foundry 文档](https://learn.foundry.com/nuke/15.1/content/comp_environment/configuring_nuke/sourcing_gizmos.html)。

## 源码运行与构建 EXE

仅开发或自行构建时需要安装 Python 3.10 或更高版本。在项目根目录执行：

```powershell
python -m pip install -r requirements.txt
python app.py
```

打包配置与构建产物统一放在 `tools` 目录。生成单文件 EXE：

```powershell
python -m pip install -r tools/requirements-build.txt
python tools/assets/prepare_app_icons.py
python -m PyInstaller --workpath tools/build --distpath tools/dist tools/WrapItUpISO.spec
```

生成的程序为 `tools/dist/WrapItUpISO 1.1.32.exe`，文件名自动采用 `version.json` 中的名称与版本，无需附带 `_internal` 文件夹。
窗口、任务栏和 EXE 使用 `wrapitupiso/resources/icons/nuke logo.png` 转换的圆形图标；工具栏使用同目录中的横版 Logo。图标资源随 EXE 内置，替换源图片后运行 `prepare_app_icons.py` 再构建即可更新。
界面使用 Qt6 / PySide6，文件拖放由 Qt 原生处理；EXE 内置 Qt 库与 Windows 平台插件，不需要额外安装 Qt。

## 语言与便携设置

一个 EXE 内置简体中文、English、日本語。在“视图 → 语言 / Language”切换，立即更新菜单、按钮、表头、已扫描结果、属性和历史日志；工程、扫描缓存与选择状态保留。任务进行时暂时禁用语言切换。首次启动跟随系统语言，其他语言默认英文，之后使用保存的选择。

应用自有文案及 Windows 文件选择器的自定义提示跟随选择的语言；原生系统按钮和系统错误可能仍使用 Windows 的语言。文件路径、节点名、插件名、品牌和程序标语保持原样。交付 `readme.md` 使用本次打包选择的语言；`report.json` 保留既有字段、状态值和中文诊断以兼容旧版，新增 `language` 字段记录交付语言。所有交付文件仍使用既定的小写命名。

启动时自动创建并读取 **程序所在目录 / `settings/wrapitupiso_settings.json`**。使用 EXE 时以 EXE 所在目录为准，源码运行时以项目根目录为准，不依赖启动时的工作目录，也不写入 PyInstaller 的临时解压目录。

设置内容包括语言、窗口大小与位置、最大化状态、三处分栏比例、表格列宽、“只看问题”筛选、输出目录和上次添加工程的目录。项目列表和扫描结果不写入设置文件。示例：

```json
{
  "schema_version": 1,
  "language": "ja",
  "layout": {
    "window": {"width": 1240, "height": 820, "maximized": false},
    "panes": [340, 880],
    "footer_panes": [600, 600],
    "vertical_panes": [460, 190],
    "project_columns": [190, 105, 50],
    "asset_columns": [140, 58, 350, 62, 54]
  },
  "only_issues": false,
  "output_directory": "",
  "last_project_directory": ""
}
```

设置更改后自动保存，关闭时再次保存。“帮助 → 打开设置目录”可定位文件；手动编辑前请先关闭程序，避免被当前窗口状态覆盖。窗口移到已断开的显示器外时，启动会把它恢复到可见屏幕。无效字段使用默认值；损坏的 JSON 会备份为 `wrapitupiso_settings.invalid-时间戳.json` 后重建。无法写入时在日志提示，本次仍可运行，但不能记住更改。未知的较新设置格式保持原文件，不覆盖。

翻译统一保存在 `wrapitupiso/resources/locales/translations.json`，结构为 `固定键 → zh-CN / en / ja → 文案`。动态值使用 `{count}`、`{filename}` 等命名占位符，各语言的占位符必须一致。文件路径等用户数据作为参数传入，不会被当作翻译键。构建时检查字典完整性，并把字典嵌入 EXE；修改源码字典后需重新构建发布版。

## 收集规则

- 保留原工程；每个工程生成“工程名 + 路径哈希”文件夹。交付包内的文件和目录名称统一小写，包括素材、字体、Gizmo、工程文件及 `_relinked`、`_relinked_relative` 后缀；工程中的对应引用同步更新，原工程副本内容保持不变。`<UDIM>` 是 Nuke 的序列标记，保留其语法大小写。
- 收集 `file`、`proxy`、`vfield_file`、`lut_file`、`cdl_file`、`ocio_config`、`font_file` 中的固定路径。
- 字体统一写入 `fonts/`：支持旧版 Text 的 `font` 路径、`font_file`、已识别的字体文件扩展名，以及 Text2 序列化的字体族 / 样式 / 文件名 / 字体索引记录。Text2 的多个字体和默认 Utopia 字体也会检查。PostScript 字体附带存在的 `.afm` / `.pfm` 度量文件，同一源字体只复制一次，不同来源的同名字体使用不同文件名。
- Text2 字体依次在工程字体目录、已发现的插件字体目录、`NUKE_FONT_PATH`、工程所记录 Nuke 版本的字体目录和系统字体目录中查找；尊重 `free_type_system_fonts`，不会执行工程中的动态字体表达式。缺失或无法解析的字体显示为“缺失 / 待处理”，保留原引用并在 README 中列明。字体查找顺序参考 [Foundry 字体文档](https://learn.foundry.com/nuke/content/comp_environment/effects/fonts_properties.html)。
- 相对路径版会在 `.nk` 的 Root 后加入固定 Tcl 字体目录设置语句，在 Text2 加载前将本包的 `fonts` 设为工程字体目录；不会添加外部启动器或安装系统字体。实测 Nuke 15.1v5 的 Text2 字体查找在中文路径下失败，请使用英文交付路径和字体文件名。在 Nuke 中另存时，字体目录会保存为当前绝对路径；另存后再次移动，请重新打包或重新设置字体目录。
- 支持 `%04d`、`%d`、`####`、`<UDIM>`；复制目录中所有匹配文件，而非只收集时间线范围。
- 序列填充按 Nuke 的文件名格式匹配：`%04d` 的宽度包含负号，支持 `-001` 和超过四位的 `10000`；同样支持 `%4d` 的空格填充。不会将多补零或错误填充的文件误算为有效帧。
- 支持 Group 内的显式素材节点、空格及中文路径。
- 以源路径去重，同一工程中重复引用只复制一次；各工程独立打包，不跨工程共享。
- 不收集 Write / DeepWrite 的渲染输出；不改变其输出路径。
- 相对输入路径以固定的 Root `project_directory` 为基准，未设置则以 `.nk` 所在目录为基准。
- 重写为 `"[file dirname [value root.name]]/media/..."`，移动时请搬走整个打包文件夹。
- 素材子目录按节点名转为小写，例如 `media/read_plate/`；Group 内使用 `group1_read1`，Gizmo 内使用 `pluginname_read1`。不同节点引用不同素材时分别存放；共享文件或重叠序列统一使用首次引用节点的目录，只复制一次。重名（含大小写差异）追加 `_2`、`_3`，同一节点的不同素材参数也按此规则区分。生成的目录名仅使用小写英文字母、数字、下划线和连字符，其他字符替换为下划线，空名回退为 `media`；素材文件名与扩展名也转为小写，节点名称保持原样。
- 缺失、动态路径保持原值，写入 `report.json`；有这些项时不能视为完整交付。
- 交付目录生成 `readme.md`，逐项列出素材、字体、插件的打包结果（成功 / 部分成功 / 未成功），外部插件标记“不打包（接收方安装）”；详细原因和完整路径保留在 `report.json`。
- 同时输出三份工程并纳入 SHA-256 校验：`project_original.nk` 保留原工程全部内容、路径、编码和换行，仍依赖原环境；`project_relinked.nk` 指向打包后素材和字体的绝对位置；`project_relinked_relative.nk` 使用相对于交付工程的位置，推荐接收方打开。`project` 为原工程名；三份文件放在同一交付目录，共用收集的素材和字体。报告通过 `original_path_script`、`relinked_script` 和 `relative_script` 分别记录文件名。
- 取消或复制失败会清理当前工程的未完成目录，已完成的其他工程保留；如果清理也被拒绝，将明确提示残留路径。
- 已有同名打包目录时跳过并报错，不覆盖旧结果。

## 检查与收集范围

不会执行工程中的 TCL、Python 或回调；不支持 `.nknc`、`.nkple`。
无法完整求值动态路径、帧动画路径和运行时生成的依赖；这类路径会标记待处理。
外部 Gizmo 及其嵌套 Gizmo 按上面的规则收集；二进制插件、系统字体、隐式 OCIO 配置/关联 LUT 和任意脚本动态生成的依赖不自动收集。
内置节点名单用于排除标准节点，包括 `EXPTool`（Exposure）、`Mirror` / `Mirror2`；未列入名单且找不到对应文件的节点会显示“未找到”，也可能是较新版本内置节点，不能直接断言缺少第三方插件。
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

打包中存在 `packaging_incomplete.txt`，仅在复制流程全部结束后删除；素材缺失及插件依赖仍需核对报告。

## 开发与验证

源码按职责放在 `wrapitupiso/`，根目录只保留启动入口和项目配置：

```text
NukeBatchPackager/
├── app.py                      GUI 启动入口
├── cli.py                      命令行启动入口（仅标准库）
├── version.json                名称与版本的唯一配置
├── requirements.txt            运行依赖
├── wrapitupiso/
│   ├── cli.py                  命令行实现
│   ├── ui/
│   │   ├── window.py           Qt6 主窗口和交互
│   │   ├── workers.py          后台扫描、打包与进度事件
│   │   ├── widgets.py          主题、拖放控件、进度条
│   │   └── project_picker.py   Windows 原生选择器
│   ├── engine/                 工程解析、依赖扫描、缓存和打包
│   ├── config/                 设置、翻译、版本与统一路径
│   └── resources/
│       ├── icons/              Logo 和窗口图标
│       └── locales/            中英日 JSON 字典
├── tests/                      核心、设置、入口与窗口测试
├── docs/                       说明文档和预览图片
├── tools/                      构建配置与开发工具
│   ├── assets/                 图标生成工具与旧资源留档
│   ├── verify/                 Nuke 验证与性能工具
│   ├── build/                  当前构建缓存
│   ├── dist/                   当前单文件 EXE
│   ├── reports/                本地验证结果
│   └── archive/                历史构建和实验文件
└── settings/                   本机设置，首次启动自动创建
```

`engine/` 不依赖 Qt：`core.py` 负责工程解析、素材收集与路径重写；`gizmo.py` 处理插件索引与依赖；`plugin_paths.py` 静态发现插件目录；`media_scan.py`、`font_scan.py`、`callback_scan.py`、`script_scan.py` 分别检查素材、字体、回调与 Tcl 命令；`scan_cache.py` 复用扫描结果；`project_discovery.py` 搜索工程；`delivery.py` 和 `package_readme.py` 生成交付内容。

`config/paths.py` 统一定位内置资源与程序目录，启动工作目录不影响资源读取。`config/settings.py` 保持设置在程序旁边，`config/i18n.py` 读取三语字典。`ui/workers.py` 通过队列向 `ui/window.py` 发送事件，由 Qt 定时器更新界面。

`version.json` 仍由窗口标题、`python cli.py --version`、EXE 文件名及 Windows 文件属性、交付说明和报告共用。生成的设置、缓存、发布包、报告和历史归档均不进入 Git。开发工具的具体用途见 [tools/README.md](../tools/README.md)。

在项目根目录执行：

```powershell
python -m unittest discover -s tests -v
```

窗口测试需要可用的桌面环境。测试覆盖素材收集、帧号填充、嵌套 Gizmo、内部素材、自定义文件参数、同名候选与注册目录优先级、循环与缺失依赖、源文件变化、校验失败清理、命令行退出码，以及窗口扫描 / 打包 / 取消与错误恢复。

已使用 Nuke 15.1v5 验证生成的嵌套 Gizmo 测试工程：移除原插件与素材，移动交付目录到中文路径，按交付说明手动注册 Gizmo 后用 Nuke 打开并校验像素值，同时验证绝对路径 Gizmo 引用的迁移、Group 自定义 File 参数的类型和迁移后的路径。验证工具只运行生成的测试探针，探针不写入交付包。可复现的验证命令（需要本机 Nuke 和有效许可，仅运行生成的测试工程）：

```powershell
python tools/verify/verify_gizmo_nuke.py --nuke "C:/Program Files/Nuke15.1v5/Nuke15.1.exe"
python tools/verify/verify_gizmo_nuke.py --nuke "C:/Program Files/Nuke15.1v5/Nuke15.1.exe" --gui
python tools/verify/verify_scan_nuke.py --nuke "C:/Program Files/Nuke15.1v5/Nuke15.1.exe"
python tools/verify/verify_delivery_nuke.py --nuke "C:/Program Files/Nuke15.1v5/Nuke15.1.exe"
```

这不替代对实际生产工程的 Nuke 加载和渲染验收。

`verify_delivery_nuke.py` 使用生成的 Text、Text2 和含字体的 Gizmo 工程，比较原工程、绝对路径版和移走源文件后的相对路径版渲染哈希；另行探测 Nuke 的中文字体目录支持情况。只运行生成的测试工程。

`verify_scan_nuke.py` 使用临时用户目录及生成的配置，比较嵌套 `pluginAddPath` / `pluginAppendPath`、pathlib 目录循环、递归扫描和辅助函数注册的实际顺序、同名与备份插件选择、六种序列格式的负帧和超位数帧，并核对移除源文件及移动交付包前后的像素值。
