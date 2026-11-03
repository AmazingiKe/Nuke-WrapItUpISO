"""Tkinter UI; workers communicate with the UI through a queue."""
from pathlib import Path
from queue import Empty, Queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from tkinterdnd2 import COPY, DND_FILES, REFUSE_DROP, TkinterDnD
from core import Cancelled, DependencyError, package, scan
from gizmo import Plugin
from app_version import VERSION
from project_picker import ask_projects


def readable_error(error):
    if isinstance(error, DependencyError):
        return '插件或文件校验未通过，请查看右侧状态'
    if isinstance(error, FileExistsError):
        return '输出已存在，请更换输出目录'
    if isinstance(error, FileNotFoundError):
        return '找不到文件，请检查路径'
    if isinstance(error, PermissionError):
        return '无法访问文件，请检查权限或文件占用'
    if isinstance(error, ValueError):
        return '工程内容无法解析，请查看右侧状态'
    if '未完成目录清理失败' in str(error):
        return '未完成目录清理失败，请查看右侧状态并手动检查'
    if isinstance(error, OSError):
        return '文件读写失败，请检查磁盘和路径'
    return '处理失败，请查看右侧状态'


def needs_review(plan):
    return bool(plan.plugin_blockers) or any(asset.status != '就绪' for asset in plan.assets) or len(plan.warnings) > 1


def brief_asset_summary(plan):
    parts = [f'{len(plan.assets)} 项素材']
    if plan.plugins:
        ready = sum(p.status == '就绪' for p in plan.plugins)
        parts.append(f'Gizmo {ready}/{len(plan.plugins)} 项就绪')
        missing = sum(p.status == '未找到' for p in plan.plugins)
        if plan.automatic_plugins and missing:
            parts.append(f'跳过缺失插件 {missing} 项')
    for status in ('缺失', '缺帧', '待处理'):
        count = sum(asset.status == status for asset in plan.assets)
        if count:
            parts.append(f'{status} {count} 项')
    if len(plan.warnings) > 1:
        parts.append('有依赖提示')
    return '，'.join(parts)


class ToolTip:
    """Short labels for icon buttons, also shown on keyboard focus."""
    def __init__(self, widget, text):
        self.widget, self.text = widget, text
        self.pending = None
        self.popup = None
        for event in ('<Enter>', '<FocusIn>'):
            widget.bind(event, self.schedule, add='+')
        for event in ('<Leave>', '<FocusOut>', '<ButtonPress>', '<Destroy>'):
            widget.bind(event, self.hide, add='+')

    def schedule(self, event=None):
        self.hide()
        self.pending = self.widget.after(450, self.show)

    def show(self):
        self.pending = None
        if not self.widget.winfo_viewable():
            return
        self.popup = tk.Toplevel(self.widget)
        self.popup.withdraw()
        self.popup.overrideredirect(True)
        self.popup.attributes('-topmost', True)
        ttk.Label(self.popup, text=self.text, padding=(8, 5),
                  relief='solid', borderwidth=1).pack()
        self.popup.update_idletasks()
        x = min(self.widget.winfo_rootx(),
                self.widget.winfo_screenwidth() - self.popup.winfo_reqwidth() - 8)
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.popup.geometry(f'+{max(0, x)}+{y}')
        self.popup.deiconify()

    def hide(self, event=None):
        if self.pending is not None:
            self.widget.after_cancel(self.pending)
            self.pending = None
        if self.popup is not None:
            self.popup.destroy()
            self.popup = None


class CombinedProgress(tk.Canvas):
    """One track: completed projects, followed by work within the current project."""
    def __init__(self, parent):
        super().__init__(parent, height=24, highlightthickness=0, background='#e4e9f0')
        self.projects_done = 0
        self.projects_total = 1
        self.task_fraction = 0
        self.animation_id = None
        self.pulse = 0
        self.bind('<Configure>', lambda event: self.redraw())

    def set_projects(self, done, total):
        self.stop_animation()
        self.projects_total = max(1, total)
        self.projects_done = max(0, min(done, self.projects_total))
        self.task_fraction = 0
        self.redraw()

    def set_tasks(self, done, total):
        self.stop_animation()
        self.task_fraction = max(0, min(done / total, 1)) if total else 0
        if not total and self.projects_done < self.projects_total:
            self.pulse = 0
            self.animate()
        else:
            self.redraw()

    def fractions(self):
        dark = self.projects_done / self.projects_total
        light = min(1, dark + self.task_fraction / self.projects_total)
        return dark, light

    def redraw(self):
        width, height = self.winfo_width(), self.winfo_height()
        dark, light = self.fractions()
        self.delete('all')
        if self.animation_id is not None:
            segment = width / self.projects_total
            start = width * dark + self.pulse * segment * 0.7
            self.create_rectangle(start, 0, start + segment * 0.3, height,
                                  fill='#9ec8e8', outline='', tags='tasks')
        else:
            self.create_rectangle(width * dark, 0, width * light, height,
                                  fill='#9ec8e8', outline='', tags='tasks')
        self.create_rectangle(0, 0, width * dark, height,
                              fill='#245780', outline='', tags='projects')
        if self.projects_total <= 30:
            for index in range(1, self.projects_total):
                x = width * index / self.projects_total
                self.create_line(x, 0, x, height, fill='#ffffff', tags='separators')

    def animate(self):
        self.pulse = (self.pulse + 0.06) % 1
        self.animation_id = self.after(60, self.animate)
        self.redraw()

    def stop_animation(self):
        if self.animation_id is not None:
            self.after_cancel(self.animation_id)
            self.animation_id = None

    def finish(self):
        self.stop_animation()
        self.task_fraction = 0
        self.redraw()

    def destroy(self):
        self.stop_animation()
        super().destroy()


class Worker(threading.Thread):
    def __init__(self, paths, output, events):
        super().__init__(daemon=False)
        self.paths = paths
        self.output = output
        self.events = events
        self.cancelled = threading.Event()
        self.last_progress = None
        self.last_progress_time = 0
        self.copy_sizes = []
        self.copied_files = 0
        self.copy_bytes = 0
        self.file_start_bytes = 0

    def emit(self, kind, *values):
        self.events.put((kind, values))

    def report_progress(self, phase, done, total, detail):
        now = time.monotonic()
        key = (phase, total)
        if key != self.last_progress or now - self.last_progress_time >= 0.05 or (total and done >= total):
            self.emit('progress', phase, done, total, detail)
            self.last_progress = key
            self.last_progress_time = now

    def file_copied(self, done, total, name):
        self.copied_files = done
        self.file_start_bytes = self.copy_bytes
        self.report_progress('复制文件', done, total, name)
        if done == total:
            self.report_progress('写入工程与报告', 0, 0, '正在完成打包')

    def copy_byte_progress(self, done, total, name):
        self.copy_bytes = done
        fraction = 0
        if self.copied_files < len(self.copy_sizes):
            size = self.copy_sizes[self.copied_files]
            fraction = min(1, max(0, done - self.file_start_bytes) / size) if size else 0
        self.report_progress('复制文件', self.copied_files + fraction, len(self.copy_sizes),
                             f'{done / 1048576:.1f}/{total / 1048576:.1f} MiB · {name}')

    def run(self):
        completed = failed = 0
        cancelled = False
        try:
            for index, path in enumerate(self.paths, 1):
                if self.cancelled.is_set():
                    cancelled = True
                    break
                self.emit('project', index, len(self.paths), path.name)
                self.emit('project_state', path, '扫描中')
                self.report_progress('读取工程', 0, 0, path.name)
                try:
                    plan = scan(path, self.cancelled.is_set,
                                progress=lambda *values: self.report_progress('扫描素材与插件', *values),
                                automatic_plugins=True)
                    self.report_progress('统计素材', 0, 0, path.name)
                    size = plan.size
                    if self.cancelled.is_set():
                        raise Cancelled()
                    self.emit('scanned', plan, size)
                    if self.output:
                        self.emit('project_state', path, '打包中')
                        self.report_progress('准备打包', 0, 0, path.name)
                        files = dict.fromkeys(file for asset in plan.assets for file in asset.files)
                        self.copy_sizes = [file.stat().st_size for file in files]
                        self.copied_files = self.copy_bytes = self.file_start_bytes = 0
                        package(
                            plan, self.output, self.cancelled.is_set,
                            self.file_copied,
                            byte_progress=self.copy_byte_progress,
                        )
                        self.emit('project_state', path, '已打包·需核对' if needs_review(plan) else '已打包')
                    action = '打包完成' if self.output else '扫描完成'
                    self.emit('message', f'{action} · {path.name} · {brief_asset_summary(plan)}',
                              'warning' if needs_review(plan) else 'success')
                    completed += 1
                except Cancelled:
                    cancelled = True
                    self.emit('project_state', path, '已取消')
                    detail = '，未完成文件已清理' if self.output else ''
                    self.emit('message', f'已取消 · {path.name}{detail}', 'warning')
                    break
                except Exception as error:
                    failed += 1
                    self.emit('project_state', path, '依赖待处理' if isinstance(error, DependencyError) else '失败', f'{type(error).__name__}: {error}')
                    self.emit('message', f'失败 · {path.name} · {readable_error(error)}', 'error')
                self.emit('project_done', completed + failed, len(self.paths))
        finally:
            self.emit('finished', completed, failed, cancelled)


class Window(TkinterDnD.Tk):
    def __init__(self):
        super().__init__()
        self.title(f'Nuke Batch Packager {VERSION} · 批量打包')
        self.geometry('1160x880')
        self.minsize(780, 640)
        self.paths = []
        self.plans = {}
        self.project_errors = {}
        self.asset_rows = {}
        self.last_project_directory = None
        self.worker = None
        self.events = Queue()
        self.closing = False
        self.poll_id = None
        self.output = tk.StringVar(self)
        self.count = tk.StringVar(self, value='已添加 0 个工程 · 已扫描 0 个')
        self.progress_text = tk.StringVar(self, value='就绪')
        self.total_text = tk.StringVar(self, value='整体进度：尚未开始')
        self._build_widgets()
        self.show_item_status()
        self.drop_target_register(DND_FILES)
        self.dnd_bind('<<Drop>>', self.on_drop)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.poll_id = self.after(50, self.poll_events)

    def _build_widgets(self):
        style = ttk.Style(self)
        if 'vista' in style.theme_names():
            style.theme_use('vista')
        style.configure('Title.TLabel', font=('Microsoft YaHei UI', 20, 'bold'))
        style.configure('Tagline.TLabel', font=('Segoe UI', 11, 'bold'))
        style.configure('TButton', padding=(10, 6))
        style.configure('Tool.TButton', padding=0, anchor='center')
        style.configure('Treeview', rowheight=27)
        root = ttk.Frame(self, padding=16)
        root.pack(fill='both', expand=True)
        ttk.Label(root, text='Nuke 批量打包器', style='Title.TLabel').pack(anchor='w')
        ttk.Label(root, text='Fuck Vendor Nuke Pack. One Click, No Bullshit.',
                  style='Tagline.TLabel', wraplength=730).pack(anchor='w', pady=(6, 16))
        output_row = ttk.Frame(root)
        output_row.pack(fill='x')
        ttk.Label(output_row, text='输出目录').pack(side='left', padx=(0, 8))
        self.output_entry = ttk.Entry(output_row, textvariable=self.output)
        self.output_entry.pack(side='left', fill='x', expand=True)
        self.choose = ttk.Button(output_row, text='...', width=3, command=self.choose_output)
        self.choose.pack(side='left', padx=(8, 0))
        for widget in (output_row, self.output_entry, self.choose):
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind('<<Drop>>', self.on_output_drop)
        self.toolbar = ttk.Frame(root)
        self.toolbar.pack(fill='x', pady=(10, 6))
        self.controls = []
        self.tooltips = []
        self.toolbar_images = []
        scale = float(self.tk.call('tk', 'scaling')) / (96 / 72)
        button_size, icon_size = round(32 * scale), round(20 * scale)

        def tool_button(icon, command, side='left'):
            # Pixel-sized slots keep every button square, regardless of image metrics.
            slot = ttk.Frame(self.toolbar, width=button_size, height=button_size)
            slot.pack(side=side, padx=(0, 4) if side == 'left' else 0)
            slot.pack_propagate(False)
            images = []
            for suffix in ('', '_disabled'):
                path = Path(__file__).parent / 'assets' / 'toolbar' / f'{icon}{suffix}.png'
                bitmap = tk.PhotoImage(master=self, file=str(path))
                if icon_size != 20:
                    bitmap = bitmap.zoom(icon_size).subsample(20)
                images.append(bitmap)
            self.toolbar_images.extend(images)
            button = ttk.Button(slot, image=(images[0], 'disabled', images[1]),
                                command=command, style='Tool.TButton')
            button.pack(fill='both', expand=True)
            return button

        for index, (icon, label, command) in enumerate([
            ('add', '添加工程 / 文件夹', self.choose_projects),
            ('clear', '清空工程列表', self.clear),
            ('scan', '扫描素材与插件', self.scan_only),
            ('pack', '开始打包', self.pack),
        ]):
            if index in (2, 3):
                ttk.Separator(self.toolbar, orient='vertical').pack(side='left', fill='y', padx=6, pady=5)
            button = tool_button(icon, command)
            self.controls.append(button)
            self.tooltips.append(ToolTip(button, label))
        self.add_project_button = self.controls[0]
        self.cancel = tool_button('stop', self.stop, side='right')
        self.cancel.state(['disabled'])
        self.tooltips.append(ToolTip(self.cancel, '取消当前任务'))
        self.panes = ttk.Panedwindow(root, orient='horizontal')
        self.panes.pack(fill='both', expand=True)
        projects = ttk.Frame(self.panes, width=330, padding=(0, 0, 10, 0))
        details = ttk.Frame(self.panes, padding=(10, 0, 0, 0))
        self.panes.add(projects, weight=1)
        self.panes.add(details, weight=3)
        ttk.Label(projects, textvariable=self.count).pack(side='bottom', anchor='w', pady=(6, 0))
        project_frame = ttk.Frame(projects)
        project_frame.pack(fill='both', expand=True)
        project_frame.rowconfigure(0, weight=1)
        project_frame.columnconfigure(0, weight=1)
        self.project_table = ttk.Treeview(project_frame, columns=('name', 'status', 'assets'),
                                          show='headings', selectmode='browse', height=6)
        for column, heading, width in [('name', '工程', 175), ('status', '状态', 85), ('assets', '素材', 55)]:
            self.project_table.heading(column, text=heading)
            self.project_table.column(column, width=width, minwidth=45, stretch=column == 'name')
        project_scroll = ttk.Scrollbar(project_frame, orient='vertical', command=self.project_table.yview)
        project_horizontal = ttk.Scrollbar(project_frame, orient='horizontal', command=self.project_table.xview)
        self.project_table.configure(yscrollcommand=project_scroll.set, xscrollcommand=project_horizontal.set)
        self.project_table.grid(row=0, column=0, sticky='nsew')
        project_scroll.grid(row=0, column=1, sticky='ns')
        project_horizontal.grid(row=1, column=0, sticky='ew')
        self.project_table.bind('<<TreeviewSelect>>', self.render_selected_project)
        table_frame = ttk.Frame(details)
        table_frame.pack(fill='both', expand=True)
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        columns = ('node', 'type', 'path', 'files', 'status')
        self.table = ttk.Treeview(table_frame, columns=columns, show='headings', height=4, selectmode='browse')
        self.table.bind('<<TreeviewSelect>>', self.show_item_status)
        self.table.bind('<Button-1>', self.clear_asset_selection_on_blank)
        for column, heading, width in zip(
            columns, ['节点', '类型', '原路径', '文件数', '状态'],
            [120, 75, 390, 65, 65],
        ):
            self.table.heading(column, text=heading)
            self.table.column(column, width=width, minwidth=55, stretch=column == 'path',
                              anchor='center' if column in {'type', 'files', 'status'} else 'w')
        vertical = ttk.Scrollbar(table_frame, orient='vertical', command=self.table.yview)
        horizontal = ttk.Scrollbar(table_frame, orient='horizontal', command=self.table.xview)
        self.table.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.table.grid(row=0, column=0, sticky='nsew')
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        status_frame = ttk.Frame(details)
        status_frame.pack(side='bottom', fill='x', before=table_frame)
        ttk.Label(status_frame, text='素材 / 插件状态').pack(anchor='w', pady=(8, 4))
        self.dependencies = ScrolledText(status_frame, height=5, wrap='word', state='disabled')
        self.dependencies.pack(fill='x')
        # Reserve the footer first, letting the split pane shrink on small windows.
        footer = ttk.Frame(root)
        footer.pack(side='bottom', fill='x', before=self.panes)
        progress_header = ttk.Frame(footer)
        progress_header.pack(fill='x', pady=(12, 0))
        ttk.Label(progress_header, text='深色：工程数  ·  浅色：当前阶段任务',
                  foreground='#245780').pack(side='right')
        ttk.Label(progress_header, textvariable=self.total_text).pack(side='left', fill='x', expand=True)
        self.progress = CombinedProgress(footer)
        self.progress.pack(fill='x', pady=(4, 4))
        ttk.Label(footer, textvariable=self.progress_text).pack(anchor='w')
        ttk.Label(footer, text='日志').pack(anchor='w', pady=(8, 4))
        self.log = ScrolledText(footer, height=5, wrap='word', state='disabled')
        for view in (self.dependencies, self.log):
            view.configure(font=('Microsoft YaHei UI', 10), padx=8, pady=6,
                           spacing1=2, spacing3=4)
            for level, color in [('info', '#334155'), ('success', '#16703c'),
                                 ('warning', '#985c00'), ('error', '#b42318')]:
                view.tag_configure(level, foreground=color)
        self.log.pack(fill='x')

    def busy(self):
        return self.worker is not None

    def append_log(self, text, level='info'):
        self.log.configure(state='normal')
        self.log.insert('end', text + '\n', (level,))
        self.log.see('end')
        self.log.configure(state='disabled')

    def clear_log(self):
        self.log.configure(state='normal')
        self.log.delete('1.0', 'end')
        self.log.configure(state='disabled')

    def add(self, paths):
        if self.busy():
            return
        for path in paths:
            path = Path(path)
            try:
                is_folder = path.is_dir()
                found = sorted(item for item in path.rglob('*.nk') if item.is_file()) if is_folder else [path]
                if is_folder and not found:
                    self.append_log(f'未找到 .nk · {path.name}', 'warning')
                for item in found:
                    item = item.resolve()
                    if item.is_file() and item.suffix.lower() == '.nk' and item not in self.paths:
                        self.paths.append(item)
                        self.project_table.insert('', 'end', iid=str(item), values=(item.name, '未扫描', '—'))
            except OSError as error:
                self.append_log(f'添加失败 · {path.name} · {readable_error(error)}', 'error')
        self.update_project_count()
        if self.paths and not self.project_table.selection():
            self.project_table.selection_set(str(self.paths[0]))
            self.project_table.focus(str(self.paths[0]))
        self.render_selected_project()

    def on_drop(self, event):
        if self.busy():
            return REFUSE_DROP
        self.add(self.tk.splitlist(event.data))
        return COPY

    def update_project_count(self):
        self.count.set(f'已添加 {len(self.paths)} 个工程 · 已扫描 {len(self.plans)} 个')

    def on_output_drop(self, event):
        if self.busy():
            return REFUSE_DROP
        try:
            paths = self.tk.splitlist(event.data)
            if len(paths) == 1 and Path(paths[0]).is_dir():
                self.output.set(str(Path(paths[0]).resolve()))
                return COPY
        except (OSError, tk.TclError):
            self.append_log('无法读取输出文件夹，请检查路径与权限。', 'error')
            return REFUSE_DROP
        self.append_log('请拖入一个文件夹作为输出目录。')
        return REFUSE_DROP

    def choose_projects(self):
        if self.busy():
            return
        initialdir = self.last_project_directory or (self.paths[0].parent if self.paths else Path.home())
        try:
            selected = ask_projects(self, initialdir)
        except (OSError, ValueError) as error:
            messagebox.showerror('无法打开文件选择器', str(error), parent=self)
            return
        if selected:
            self.last_project_directory = Path(selected[0]).parent
            self.add(selected)

    def choose_output(self):
        path = filedialog.askdirectory(parent=self, title='选择输出目录')
        if path:
            self.output.set(path)


    def clear(self):
        if self.busy():
            return
        self.paths.clear()
        self.plans.clear()
        self.project_errors.clear()
        self.project_table.delete(*self.project_table.get_children())
        self.render_selected_project()
        self.update_project_count()
        self.clear_log()

    def scan_only(self):
        self.start(None)

    def pack(self):
        output = self.output.get().strip()
        if not output:
            messagebox.showinfo('输出目录', '请先选择输出目录。', parent=self)
            return
        self.start(output)

    def start(self, output):
        if self.busy():
            return
        if not self.paths:
            messagebox.showinfo('添加工程', '请先添加 .nk 工程。', parent=self)
            return
        self.clear_log()
        action = '打包' if output else '扫描'
        self.append_log(f'开始{action} · 共 {len(self.paths)} 个工程')
        self.plans.clear()
        self.project_errors.clear()
        self.update_project_count()
        for path in self.paths:
            self.project_table.item(str(path), values=(path.name, '等待扫描', '—'))
        self.render_selected_project()
        self.progress.set_projects(0, len(self.paths))
        self.total_text.set(f'整体进度：0/{len(self.paths)} 个工程')
        self.progress.set_tasks(0, 0)
        self.progress_text.set('正在扫描…')
        for control in self.controls + [self.choose, self.output_entry]:
            control.state(['disabled'])
        self.cancel.state(['!disabled'])
        self.worker = Worker(self.paths.copy(), output, self.events)
        self.worker.start()

    def poll_events(self):
        self.poll_id = None
        handlers = {
            'message': self.append_log, 'scanned': self.show_plan,
            'progress': self.show_progress, 'finished': self.finished,
            'project': self.show_project, 'project_done': self.project_done,
            'project_state': self.set_project_state,
        }
        # Bound batches so large queues do not starve redraws or cancellation.
        for _ in range(200):
            try:
                kind, values = self.events.get_nowait()
            except Empty:
                break
            try:
                handlers[kind](*values)
            except Exception as error:
                self.append_log(f'界面更新失败：{error}')
            if kind == 'finished':
                if self.closing:
                    self.destroy()
                    return
                break
        self.poll_id = self.after(50, self.poll_events)

    def show_plan(self, plan, size):
        self.plans[plan.script] = (plan, size)
        self.update_project_count()
        self.project_table.set(str(plan.script), 'assets', len(plan.assets))
        self.set_project_state(plan.script, '需核对' if needs_review(plan) else '已扫描')
        if self.project_table.selection() == (str(plan.script),):
            self.render_selected_project()

    def set_project_state(self, path, status, error=''):
        self.project_table.set(str(path), 'status', status)
        if error:
            self.project_errors[path] = error
        if self.project_table.selection() == (str(path),):
            self.show_item_status()

    def render_selected_project(self, event=None):
        self.table.delete(*self.table.get_children())
        self.asset_rows.clear()
        selection = self.project_table.selection()
        if selection:
            cached = self.plans.get(Path(selection[0]))
            if cached:
                plan, _ = cached
                for asset in plan.assets:
                    row = self.table.insert('', 'end', values=(
                        asset.node, '素材', asset.raw, len(asset.files), '√' if asset.status == '就绪' else '×',
                    ))
                    self.asset_rows[row] = asset
                for plugin in plan.plugins:
                    row = self.table.insert('', 'end', values=(
                        plugin.name, '插件', plugin.source or '；'.join(plugin.candidates),
                        1 if plugin.source else 0, '√' if plugin.status == '就绪' else '×',
                    ))
                    self.asset_rows[row] = plugin
        self.show_item_status()

    def clear_asset_selection_on_blank(self, event):
        if not self.table.identify_row(event.y):
            self.table.selection_remove(*self.table.selection())

    def show_item_status(self, event=None):
        selection = self.project_table.selection()
        if not selection:
            lines = [('请选择左侧工程。', 'info'), ('点击素材，可查看详细状态。', 'info')]
        else:
            path = Path(selection[0])
            status = self.project_table.set(selection[0], 'status')
            error = self.project_errors.get(path, '')
            cached = self.plans.get(path)
            selected_assets = self.table.selection()
            asset = self.asset_rows.get(selected_assets[0]) if selected_assets else None
            if isinstance(asset, Plugin):
                level = 'success' if asset.status == '就绪' else 'warning'
                lines = [(f'插件状态：{asset.status} · {asset.name}', level),
                         (asset.note, level), (f'来源：{asset.source or "尚未确定"}', 'info')]
                if asset.status != '就绪':
                    if cached and cached[0].automatic_plugins and asset.status == '未找到':
                        lines.append(('此插件将跳过收集，其余内容继续打包。', 'warning'))
                    elif asset.status in {'同名冲突', '未找到'}:
                        lines.append(('此依赖未解决，工程不能打包。', 'warning'))
                    else:
                        lines.append(('此依赖未解决，工程不能打包。', 'warning'))
                        lines.append(('请按上方原因处理内部素材、脚本或插件依赖，然后重新扫描。', 'info'))
                lines.extend((f'候选：{candidate}', 'info') for candidate in asset.candidates)
                lines.extend((f'引用位置：{owner}', 'info') for owner in asset.used_by)
                if error:
                    lines.append((error, 'error'))
            elif asset is not None:
                default_notes = {'就绪': '素材文件已找到。', '缺失': '未找到匹配的素材文件。',
                                 '缺帧': '序列存在缺帧，请核对声明的帧范围。', '待处理': '需人工核对路径或表达式。'}
                level = 'success' if asset.status == '就绪' else ('error' if asset.status == '缺失' else 'warning')
                lines = [
                    (f'素材状态：{asset.status} · 文件数：{len(asset.files)}', level),
                    (f'说明：{asset.note or default_notes.get(asset.status, "请核对素材状态。")}', level),
                ]
                if asset.missing:
                    frames = ', '.join(str(frame) for frame in asset.missing[:50])
                    missing_text = f'缺帧（共 {len(asset.missing)} 帧）：{frames}'
                    if len(asset.missing) > 50:
                        missing_text += '…（仅显示前 50 帧）'
                    lines.append((missing_text, 'warning'))
                if error:
                    lines.append((f'工程处理失败：{error}', 'error'))
                lines.extend([
                    (f'节点：{asset.node} · 参数：{asset.knob}', 'info'),
                    (f'原路径：{asset.raw}', 'info'),
                    (f'解析路径：{asset.source or "尚未解析"}', 'info'),
                ])
            elif cached is None:
                lines = [(f'工程状态：{status}', 'error' if error else 'info')]
                if error:
                    lines.append((error, 'error'))
                if not error:
                    lines.append(('点击“扫描素材与插件”查看依赖。', 'info'))
                lines.append((f'工程：{path}', 'info'))
            else:
                plan, size = cached
                issues = sum(asset.status != '就绪' for asset in plan.assets)
                lines = [
                    (f'工程状态：{status}', 'error' if error else ('warning' if needs_review(plan) else 'info')),
                    (f'{len(plan.assets)} 项素材 · 需核对 {issues} 项 · {size / 1048576:.1f} MiB', 'warning' if issues else 'info'),
                ]
                if error:
                    lines.append((error, 'error'))
                lines.append((f'插件与外部依赖：{len(plan.plugins)} 项 · 待处理 {len(plan.plugin_blockers)} 项',
                              'warning' if plan.plugin_blockers else 'info'))
                lines.append((f'已自动搜索 {len(plan.search_paths)} 个插件目录', 'info'))
                lines.extend((warning, 'warning') for warning in plan.warnings)
                lines.append((f'工程：{path}', 'info'))
        self.dependencies.configure(state='normal')
        self.dependencies.delete('1.0', 'end')
        for text, level in lines:
            self.dependencies.insert('end', text + '\n', (level,))
        self.dependencies.yview_moveto(0)
        self.dependencies.configure(state='disabled')

    def show_project(self, index, total, name):
        self.progress.set_projects(index - 1, total)
        self.progress.set_tasks(0, 0)
        self.total_text.set(f'已处理 {index - 1}/{total} 个工程 · 当前 {index}/{total} · {name}')

    def project_done(self, done, total):
        self.progress.set_projects(done, total)

    def show_progress(self, phase, done, total, detail):
        if self.worker and self.worker.cancelled.is_set():
            return
        self.progress.set_tasks(done, total)
        if total:
            percent = min(100, done / total * 100)
            amount = f'任务 {int(done)}/{total}'
            self.progress_text.set(f'{phase} · {percent:.1f}% · {amount} · {detail}')
        else:
            self.progress_text.set(f'{phase} · 正在统计或处理任务 · {detail}')

    def stop(self):
        if self.worker:
            self.worker.cancelled.set()
            self.cancel.state(['disabled'])
            self.progress_text.set('正在取消并清理…')

    def finished(self, completed, failed, cancelled):
        self.worker.join()
        self.worker = None
        for path in self.paths:
            if self.project_table.set(str(path), 'status') == '等待扫描':
                self.set_project_state(path, '未扫描')
        for control in self.controls + [self.choose, self.output_entry]:
            control.state(['!disabled'])
        self.cancel.state(['disabled'])
        self.progress.set_projects(completed + failed, len(self.paths))
        self.progress.finish()
        outcome = '已取消' if cancelled else ('结束，存在失败' if failed else '任务完成')
        self.progress_text.set(outcome)
        self.total_text.set(f'{outcome} · 成功 {completed} · 失败 {failed} · 共 {len(self.paths)} 个工程')
        review_count = sum(needs_review(plan) for plan, _ in self.plans.values())
        summary = f'{"已取消" if cancelled else "本轮结束"} · 完成 {completed} 个工程'
        if failed:
            summary += f'，失败 {failed} 个'
        if cancelled:
            summary += f'，未完成 {len(self.paths) - completed - failed} 个'
        if review_count:
            summary += f'，{review_count} 个需核对'
        if failed or review_count:
            summary += '（见右侧状态）'
        self.append_log(summary, 'error' if failed else ('warning' if cancelled or review_count else 'success'))

    def close(self):
        if self.busy():
            self.closing = True
            self.stop()
            self.append_log('正在取消，清理完成后自动关闭。')
        else:
            self.destroy()

    def destroy(self):
        if self.poll_id is not None:
            self.after_cancel(self.poll_id)
            self.poll_id = None
        super().destroy()


if __name__ == '__main__':
    Window().mainloop()
