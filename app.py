"""Tkinter UI; workers communicate with the UI through a queue."""
from pathlib import Path
from queue import Empty, Queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from tkinterdnd2 import COPY, DND_FILES, REFUSE_DROP, TkinterDnD
from core import Cancelled, package, scan
from app_version import VERSION


def readable_error(error):
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
    return any(asset.status != '就绪' for asset in plan.assets) or len(plan.warnings) > 1


def brief_asset_summary(plan):
    parts = [f'{len(plan.assets)} 项素材']
    for status in ('缺失', '缺帧', '待处理'):
        count = sum(asset.status == status for asset in plan.assets)
        if count:
            parts.append(f'{status} {count} 项')
    if len(plan.warnings) > 1:
        parts.append('有依赖提示')
    return '，'.join(parts)


class Worker(threading.Thread):
    def __init__(self, paths, output, events):
        super().__init__(daemon=False)
        self.paths = paths
        self.output = output
        self.events = events
        self.cancelled = threading.Event()
        self.last_progress = None
        self.last_progress_time = 0

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
        if done == total:
            self.report_progress('写入工程与报告', 0, 0, '正在完成打包')

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
                                progress=lambda *values: self.report_progress('扫描节点', *values))
                    self.report_progress('统计素材', 0, 0, path.name)
                    size = plan.size
                    if self.cancelled.is_set():
                        raise Cancelled()
                    self.emit('scanned', plan, size)
                    if self.output:
                        self.emit('project_state', path, '打包中')
                        self.report_progress('准备打包', 0, 0, path.name)
                        package(
                            plan, self.output, self.cancelled.is_set,
                            self.file_copied,
                            byte_progress=lambda *values: self.report_progress('复制素材', *values),
                        )
                        self.emit('project_state', path, '已打包')
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
                    self.emit('project_state', path, '失败', f'{type(error).__name__}: {error}')
                    self.emit('message', f'失败 · {path.name} · {readable_error(error)}', 'error')
                self.emit('project_done', completed + failed, len(self.paths))
        finally:
            self.emit('finished', completed, failed, cancelled)


class Window(TkinterDnD.Tk):
    def __init__(self):
        super().__init__()
        self.title(f'Nuke Batch Packager {VERSION} · 离线批量打包')
        self.geometry('1160x880')
        self.minsize(780, 640)
        self.paths = []
        self.plans = {}
        self.project_errors = {}
        self.asset_rows = {}
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
        style.configure('Treeview', rowheight=27)
        root = ttk.Frame(self, padding=16)
        root.pack(fill='both', expand=True)
        ttk.Label(root, text='Nuke 批量打包器', style='Title.TLabel').pack(anchor='w')
        ttk.Label(root, text='Fuck Vendor Nuke Pack. One Click, No Bullshit.',
                  style='Tagline.TLabel', wraplength=730).pack(anchor='w', pady=(6, 16))
        bar = ttk.Frame(root)
        bar.pack(fill='x', pady=(0, 12))
        self.controls = []
        for text, command in [
            ('添加工程', self.add_files), ('添加文件夹', self.add_folder),
            ('清空', self.clear), ('扫描素材', self.scan_only), ('开始打包', self.pack),
        ]:
            button = ttk.Button(bar, text=text, command=command)
            button.pack(side='left', padx=(0, 8))
            self.controls.append(button)
        self.cancel = ttk.Button(bar, text='取消', command=self.stop, state='disabled')
        self.cancel.pack(side='right')
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
        self.panes = ttk.Panedwindow(root, orient='horizontal')
        self.panes.pack(fill='both', expand=True, pady=(12, 0))
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
        columns = ('node', 'knob', 'path', 'status', 'files', 'note')
        self.table = ttk.Treeview(table_frame, columns=columns, show='headings', height=4, selectmode='browse')
        self.table.bind('<<TreeviewSelect>>', self.show_item_status)
        self.table.bind('<Button-1>', self.clear_asset_selection_on_blank)
        for column, heading, width in zip(
            columns, ['节点', '参数', '原路径', '状态', '文件数', '说明'],
            [100, 70, 240, 70, 60, 180],
        ):
            self.table.heading(column, text=heading)
            self.table.column(column, width=width, minwidth=55)
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
        ttk.Label(footer, textvariable=self.total_text).pack(anchor='w', pady=(12, 0))
        self.total_progress = ttk.Progressbar(footer, mode='determinate', maximum=1)
        self.total_progress.pack(fill='x', pady=(4, 0))
        self.progress = ttk.Progressbar(footer, mode='determinate', maximum=1)
        self.progress.pack(fill='x', pady=(12, 4))
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
                found = sorted(path.rglob('*.nk')) if path.is_dir() else [path]
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

    def add_files(self):
        self.add(filedialog.askopenfilenames(parent=self, title='选择 Nuke 工程', filetypes=[('Nuke', '*.nk')]))

    def add_folder(self):
        path = filedialog.askdirectory(parent=self, title='选择工程目录')
        if path:
            self.add([path])

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
        self.total_progress.configure(maximum=len(self.paths), value=0)
        self.total_text.set(f'整体进度：0/{len(self.paths)} 个工程')
        self.progress.configure(mode='indeterminate', value=0)
        self.progress.start(12)
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
        self.set_project_state(plan.script, '需核对' if any(a.status != '就绪' for a in plan.assets) else '已扫描')
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
                        asset.node, asset.knob, asset.raw, asset.status, len(asset.files), asset.note,
                    ))
                    self.asset_rows[row] = asset
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
            if asset is not None:
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
                    lines.append(('点击“扫描素材”查看依赖。', 'info'))
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
                lines.append(('插件与外部依赖：需人工核对', 'warning'))
                lines.extend((warning, 'warning') for warning in plan.warnings)
                lines.append((f'工程：{path}', 'info'))
        self.dependencies.configure(state='normal')
        self.dependencies.delete('1.0', 'end')
        for text, level in lines:
            self.dependencies.insert('end', text + '\n', (level,))
        self.dependencies.yview_moveto(0)
        self.dependencies.configure(state='disabled')

    def show_project(self, index, total, name):
        self.total_text.set(f'当前工程 {index}/{total} · {name}')

    def project_done(self, done, total):
        self.total_progress.configure(maximum=total, value=done)

    def show_progress(self, phase, done, total, detail):
        if self.worker and self.worker.cancelled.is_set():
            return
        self.progress.stop()
        if total:
            percent = min(100, done / total * 100)
            self.progress.configure(mode='determinate', maximum=100, value=percent)
            amount = f'{done / 1048576:.1f}/{total / 1048576:.1f} MiB' if phase == '复制素材' else f'{done}/{total} 节点'
            self.progress_text.set(f'{phase} · {percent:.1f}% · {amount} · {detail}')
        else:
            self.progress.configure(mode='indeterminate', value=0)
            self.progress.start(12)
            self.progress_text.set(f'{phase} · {detail}')

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
        self.progress.stop()
        self.progress.configure(mode='determinate', maximum=100, value=0 if cancelled or failed else 100)
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
