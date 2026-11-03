"""Tkinter UI; workers communicate with the UI through a queue."""
from pathlib import Path
from queue import Empty, Queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from tkinterdnd2 import DND_FILES, TkinterDnD
from core import Cancelled, package, scan
from app_version import VERSION


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
                self.report_progress('读取工程', 0, 0, path.name)
                try:
                    self.emit('message', '扫描：' + str(path))
                    plan = scan(path, self.cancelled.is_set,
                                progress=lambda *values: self.report_progress('扫描节点', *values))
                    self.report_progress('统计素材', 0, 0, path.name)
                    size = plan.size
                    if self.cancelled.is_set():
                        raise Cancelled()
                    self.emit('scanned', plan, size)
                    if self.output:
                        self.report_progress('准备打包', 0, 0, path.name)
                        result = package(
                            plan, self.output, self.cancelled.is_set,
                            self.file_copied,
                            byte_progress=lambda *values: self.report_progress('复制素材', *values),
                        )
                        issues = sum(asset.status != '就绪' for asset in plan.assets)
                        self.emit('message', f'已生成：{result}；需核对 {issues} 项，另请检查插件依赖提示。')
                    completed += 1
                except Cancelled:
                    cancelled = True
                    self.emit('message', '操作已取消，当前工程的未完成文件已清理。')
                    break
                except Exception as error:
                    failed += 1
                    self.emit('message', f'失败：{path}\n{type(error).__name__}: {error}')
                self.emit('project_done', completed + failed, len(self.paths))
        finally:
            self.emit('finished', completed, failed, cancelled)


class Window(TkinterDnD.Tk):
    def __init__(self):
        super().__init__()
        self.title(f'Nuke Batch Packager {VERSION} · 离线批量打包')
        self.geometry('1160x760')
        self.minsize(780, 520)
        self.paths = []
        self.worker = None
        self.events = Queue()
        self.closing = False
        self.poll_id = None
        self.output = tk.StringVar(self)
        self.count = tk.StringVar(self, value='尚未添加工程')
        self.progress_text = tk.StringVar(self, value='就绪')
        self.total_text = tk.StringVar(self, value='整体进度：尚未开始')
        self._build_widgets()
        self.drop_target_register(DND_FILES)
        self.dnd_bind('<<Drop>>', self.on_drop)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.poll_id = self.after(50, self.poll_events)

    def _build_widgets(self):
        style = ttk.Style(self)
        if 'vista' in style.theme_names():
            style.theme_use('vista')
        style.configure('Title.TLabel', font=('Microsoft YaHei UI', 20, 'bold'))
        style.configure('TButton', padding=(10, 6))
        style.configure('Treeview', rowheight=27)
        root = ttk.Frame(self, padding=16)
        root.pack(fill='both', expand=True)
        ttk.Label(root, text='Nuke 批量打包器', style='Title.TLabel').pack(anchor='w')
        ttk.Label(root, text='无需启动 Nuke · 拖入 .nk / 文件夹 · 自动收集素材与重写路径').pack(anchor='w', pady=(4, 16))
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
        self.choose = ttk.Button(output_row, text='浏览', command=self.choose_output)
        self.choose.pack(side='left', padx=(8, 0))
        ttk.Label(root, textvariable=self.count).pack(anchor='w', pady=10)
        table_frame = ttk.Frame(root)
        table_frame.pack(fill='both', expand=True)
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        columns = ('project', 'node', 'knob', 'path', 'status', 'files', 'note')
        self.table = ttk.Treeview(table_frame, columns=columns, show='headings')
        for column, heading, width in zip(
            columns, ['工程', '节点', '参数', '原路径', '状态', '文件数', '说明'],
            [160, 120, 90, 300, 80, 65, 280],
        ):
            self.table.heading(column, text=heading)
            self.table.column(column, width=width, minwidth=55)
        vertical = ttk.Scrollbar(table_frame, orient='vertical', command=self.table.yview)
        horizontal = ttk.Scrollbar(table_frame, orient='horizontal', command=self.table.xview)
        self.table.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.table.grid(row=0, column=0, sticky='nsew')
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        ttk.Label(root, textvariable=self.total_text).pack(anchor='w', pady=(12, 0))
        self.total_progress = ttk.Progressbar(root, mode='determinate', maximum=1)
        self.total_progress.pack(fill='x', pady=(4, 0))
        self.progress = ttk.Progressbar(root, mode='determinate', maximum=1)
        self.progress.pack(fill='x', pady=(12, 4))
        ttk.Label(root, textvariable=self.progress_text).pack(anchor='w')
        self.log = ScrolledText(root, height=7, wrap='word', state='disabled')
        self.log.pack(fill='x', pady=10)
        ttk.Label(root, text='离线限制：TCL 动态路径、外部 Gizmo / 插件、隐式 OCIO 依赖需人工核对。Write 输出不收集。', wraplength=730).pack(anchor='w')

    def busy(self):
        return self.worker is not None

    def append_log(self, text):
        self.log.configure(state='normal')
        self.log.insert('end', text + '\n')
        self.log.see('end')
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
            except OSError as error:
                self.append_log(f'添加失败：{path}；{error}')
        self.count.set(f'已添加 {len(self.paths)} 个工程')

    def on_drop(self, event):
        self.add(self.tk.splitlist(event.data))

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
        self.table.delete(*self.table.get_children())
        self.count.set('尚未添加工程')
        self.log.configure(state='normal')
        self.log.delete('1.0', 'end')
        self.log.configure(state='disabled')

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
        self.table.delete(*self.table.get_children())
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
        for asset in plan.assets:
            self.table.insert('', 'end', values=(
                plan.script.name, asset.node, asset.knob, asset.raw,
                asset.status, len(asset.files), asset.note,
            ))
        self.append_log(f'{plan.script.name}：{len(plan.assets)} 项引用，约 {size / 1024 / 1024:.1f} MiB')
        for warning in plan.warnings:
            self.append_log('提示：' + warning)

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
        for control in self.controls + [self.choose, self.output_entry]:
            control.state(['!disabled'])
        self.cancel.state(['disabled'])
        self.progress.stop()
        self.progress.configure(mode='determinate', maximum=100, value=0 if cancelled or failed else 100)
        outcome = '已取消' if cancelled else ('结束，存在失败' if failed else '任务完成')
        self.progress_text.set(outcome)
        self.total_text.set(f'{outcome} · 成功 {completed} · 失败 {failed} · 共 {len(self.paths)} 个工程')
        self.append_log(self.total_text.get() + '；素材完整性请核对列表与报告。')

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
