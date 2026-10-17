"""Native Windows interface for the project's Comfy CLI generation adapter."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import queue
import shutil
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .comfy3d import Parameters, PROJECT
from .gui_jobs import Jobs, read_json, recent_runs, write_json
from .image_queue import ImageQueue
from .risk_version import RISK_VERSION

BG, CARD, FIELD = "#101722", "#1b2635", "#263549"
TEXT, MUTED, ACCENT = "#edf3fb", "#a9b8ce", "#6ae1b8"
STAGES = {"starting_server": "Запускаю ComfyUI", "server_ready": "Сервер готов",
          "uploading": "Загружаю изображение", "building": "Подготавливаю параметры",
          "submitting": "Отправляю задание", "submitted": "Задание принято. Генерация",
          "generating": "Генерация", "downloading": "Сохраняю GLB",
          "releasing_models": "GLB готов. Освобождаю видеопамять",
          "rendering_preview": "GLB готов. Создаю предпросмотр",
          'risk_cleanup': 'Освобождаю память неудачной попытки',
          'retrying_structure': 'Повтор с новым seed структуры'}
NODE_NAMES = {"3": "Структура модели", "18": "Базовая форма", "23": "Детализация 1024",
              "12": "Текстуры", "94": "Увеличение детализации", "119": "Декодирование структуры",
              "241": "Remesh", "186": "Упрощение сетки", "196": "UV-развёртка",
              "147": "Запекание текстур", "224": "Карта нормалей", "233": "Карта AO",
              "92": "Геометрия без текстур", "322": "Экспорт GLB"}
PREVIEW_STAGES = {"structure": "Структура объекта", "geometry": "Геометрия без текстур",
                  "prepared": "Подготовленная сетка", "textured": "Модель с базовой текстурой"}
QUALITY_LABELS = {'Быстро · 512': 'fast', 'Проверенный · 1024': 'standard'}
RISK_LABELS = {'low': 'Низкий', 'medium': 'Средний', 'high': 'Высокий', 'unknown': 'Неясно'}


def duration(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds//3600:02d}:{seconds//60%60:02d}:{seconds%60:02d}"


def completion_sound():
    if os.name == "nt":
        import winsound
        for frequency, duration in ((880, 120), (1100, 120), (1320, 220)):
            winsound.Beep(frequency, duration)


class App:
    def __init__(self, root: tk.Tk, project: Path = PROJECT, *, poll: bool = True):
        self.root, self.project = root, project.resolve()
        self.jobs = Jobs(self.project)
        self.settings_path = self.project / ".state/gui/settings.json"
        self.settings = read_json(self.settings_path)
        self.image_queue = ImageQueue(self.project / '.state/gui/queue.json')
        self.queue_explicit_empty = self.image_queue.path.exists() and not self.image_queue.items
        self.queue_running = False
        self.queue_current = None
        self.queue_parameters = None
        self.import_pending = []
        self.checking_images = False
        self.image = None
        self.result = None
        self.model_viewer = None
        self.image_loading = False
        self.preview_loading = False
        self.low_poly_running = False
        self.yaw = 45
        self.current_node = None
        self.started = None
        self.stage_started = None
        self.last_progress_at = None
        self.last_progress_clock = None
        self.phase_key = None
        self.final_ready = False
        self.intermediate_shown = False
        self.photos = {}
        self.canvas_images = {}
        self.status = tk.StringVar(value="Выберите картинку или вставьте её из буфера обмена")
        self.step_text = tk.StringVar(value="Готов к работе")
        self.elapsed = tk.StringVar(value="")
        self.activity_text = tk.StringVar(value="")
        size = str(self.settings.get('preview_size', 256))
        self.preview_size = tk.StringVar(value=size if size in ('128', '256', '512') else '256')
        self.jobs.preview_size = int(self.preview_size.get())
        self.sound = tk.BooleanVar(value=self.settings.get("sound", True))
        self.variables = {}
        self.parameter_widgets = []
        root.title("Dart3s — Генерация 3D")
        root.geometry("1240x860")
        root.minsize(1120, 800)
        root.configure(bg=BG)
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.bind("<Control-v>", self.paste_shortcut)
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=CARD)
        style.configure("TLabel", background=CARD, foreground=TEXT, font=("Segoe UI", 10))
        style.configure("Muted.TLabel", foreground=MUTED, font=("Segoe UI", 9))
        style.configure("TButton", font=("Segoe UI", 10), padding=(12, 8), background=FIELD, foreground=TEXT)
        style.map("TButton", background=[("active", "#3b5069"), ("disabled", "#202c3c")],
                  foreground=[("disabled", "#6d7b8d")])
        style.configure("Accent.TButton", background=ACCENT, foreground="#102a24", font=("Segoe UI", 11, "bold"))
        style.map("Accent.TButton", background=[("active", "#a0f3d7"), ("disabled", "#263d3c")])
        style.configure("TEntry", fieldbackground=FIELD, foreground=TEXT, insertcolor=TEXT, padding=3)
        style.configure("TCombobox", fieldbackground=FIELD, foreground=TEXT, padding=3)
        style.map("TCombobox", fieldbackground=[("readonly", FIELD)], foreground=[("readonly", TEXT)])
        style.configure("TCheckbutton", background=CARD, foreground=TEXT, font=("Segoe UI", 9))
        style.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=FIELD)
        style.configure("Treeview", background=CARD, fieldbackground=CARD, foreground=TEXT, rowheight=28)
        style.configure("Treeview.Heading", background=FIELD, foreground=TEXT)
        header = tk.Frame(root, bg=BG)
        header.pack(fill="x", padx=26, pady=(20, 16))
        tk.Label(header, text="Dart3s", font=("Segoe UI", 24, "bold"), fg=TEXT, bg=BG).pack(side="left")
        tk.Label(header, text="  Картинка → 3D-модель", font=("Segoe UI", 12), fg=MUTED, bg=BG).pack(side="left", pady=(10, 0))
        ttk.Button(header, text="История", command=self.history).pack(side="right")
        body = tk.Frame(root, bg=BG)
        body.pack(fill="both", expand=True, padx=24)
        body.grid_columnconfigure(0, weight=1, uniform="media")
        body.grid_columnconfigure(1, weight=0, minsize=272)
        body.grid_columnconfigure(2, weight=1, uniform="media")
        body.grid_rowconfigure(0, weight=1)
        source = ttk.Frame(body, style="Card.TFrame", padding=18)
        source.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        self.heading(source, "1. Исходное изображение")
        self.source_canvas = tk.Canvas(source, bg="#111b28", highlightthickness=0, height=345)
        self.source_canvas.pack(fill="both", expand=True, pady=(15, 10))
        self.placeholder(self.source_canvas, "Выберите изображение\nили нажмите Ctrl+V")
        self.source_label = ttk.Label(source, text="PNG · JPG · WebP · BMP", style="Muted.TLabel", wraplength=335)
        self.source_label.pack(fill="x", pady=(0, 12))
        source_controls = ttk.Frame(source, style="Card.TFrame")
        source_controls.pack(fill="x")
        self.choose_button = ttk.Button(source_controls, text="Добавить картинки", command=self.choose_image)
        self.choose_button.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.paste_button = ttk.Button(source_controls, text="Вставить", command=self.paste_image)
        self.paste_button.pack(side="left", fill="x", expand=True)
        self.queue_label = ttk.Label(source, style='Muted.TLabel')
        self.queue_label.pack(fill='x', pady=(10, 4))
        queue_frame = ttk.Frame(source, style='Card.TFrame')
        queue_frame.pack(fill='x')
        self.queue_table = ttk.Treeview(queue_frame, columns=('name', 'risk', 'status'), show='headings', height=4,
                                       selectmode='browse')
        self.queue_table.heading('name', text='Изображение')
        self.queue_table.heading('status', text='Состояние')
        self.queue_table.heading('risk', text='Риск')
        self.queue_table.column('risk', width=65, minwidth=55, stretch=False)
        self.queue_table.column('name', width=180, minwidth=80)
        self.queue_table.column('status', width=95, minwidth=70, stretch=False)
        self.queue_table.pack(side='left', fill='both', expand=True)
        scroll = ttk.Scrollbar(queue_frame, orient='vertical', command=self.queue_table.yview)
        scroll.pack(side='right', fill='y')
        self.queue_table.configure(yscrollcommand=scroll.set)
        self.queue_table.bind('<Double-1>', self.open_queue_item)
        for level, color in [('high', '#ff9b90'), ('medium', '#f0ce7a'), ('low', ACCENT), ('unknown', MUTED)]:
            self.queue_table.tag_configure(level, foreground=color)
        queue_controls = ttk.Frame(source, style='Card.TFrame')
        queue_controls.pack(fill='x', pady=(4, 0))
        ttk.Button(queue_controls, text='↑', width=3, command=lambda: self.move_queue_item(-1)).pack(side='left')
        ttk.Button(queue_controls, text='↓', width=3, command=lambda: self.move_queue_item(1)).pack(side='left', padx=4)
        ttk.Button(queue_controls, text='Убрать', command=self.remove_queue_item).pack(side='left')
        ttk.Button(queue_controls, text='Убрать готовые', command=self.clear_finished_queue).pack(side='right')
        self.check_button = ttk.Button(source, text='Проверить риски · без генерации', command=self.check_queue)
        self.check_button.pack(fill='x', pady=(6, 0))
        params = ttk.Frame(body, style="Card.TFrame", padding=18)
        params.grid(row=0, column=1, sticky="nsew", padx=(0, 10))
        self.heading(params, "2. Параметры")
        actions = ttk.Frame(params, style='Card.TFrame')
        actions.pack(side='bottom', fill='x', pady=(8, 0))
        parameter_area = ttk.Frame(params, style='Card.TFrame')
        parameter_area.pack(fill='both', expand=True)
        parameter_canvas = tk.Canvas(parameter_area, bg=CARD, highlightthickness=0, width=228, height=1)
        parameter_scroll = ttk.Scrollbar(parameter_area, orient='vertical', command=parameter_canvas.yview)
        parameter_scroll.pack(side='right', fill='y')
        parameter_canvas.pack(side='left', fill='both', expand=True)
        parameter_canvas.configure(yscrollcommand=parameter_scroll.set)
        params = ttk.Frame(parameter_canvas, style='Card.TFrame')
        parameter_window = parameter_canvas.create_window((0, 0), window=params, anchor='nw')
        params.bind('<Configure>', lambda e: parameter_canvas.configure(scrollregion=parameter_canvas.bbox('all')))
        parameter_canvas.bind('<Configure>', lambda e: parameter_canvas.itemconfigure(parameter_window, width=e.width))
        self.parameter(params, "Режим качества", "quality", "Быстро · 512", list(QUALITY_LABELS))
        self.parameter_widgets[-1][0].bind('<<ComboboxSelected>>', self.change_quality)
        self.parameter(params, "Модель", "model", "Trellis2", ["Trellis2", "Pixal3D"])
        self.parameter(params, "Полигонов для low poly копии", "polygons", "10000")
        # Original geometry is the primary result. Legacy settings remain in
        # metadata for older workflows, but do not affect this generation.
        self.variables['remesh_resolution'] = tk.StringVar(value='256')
        self.variables['texture_size'] = tk.StringVar(value='256')
        self.parameter(params, "Seed структуры", "structure_seed", "56")
        self.parameter(params, "Seed формы", "shape_seed", "42")
        self.parameter(params, "Seed текстуры", "texture_seed", "43")
        self.parameter(params, "Лимит структуры · 0 = выкл.", "max_structure_elements", "4096", ["0", "2048", "4096", "6144", "8192"])
        early_row = ttk.Frame(params, style='Card.TFrame')
        early_row.pack(fill='x')
        repeat_panel = ttk.Frame(early_row, style='Card.TFrame')
        repeat_panel.pack(side='left', fill='x', expand=True, padx=(0, 5))
        budget_panel = ttk.Frame(early_row, style='Card.TFrame')
        budget_panel.pack(side='left', fill='x', expand=True)
        self.parameter(repeat_panel, "Повторов", "structure_retries", "1", ["0", "1"])
        self.parameter_widgets[-1][0].configure(width=4)
        self.parameter(budget_panel, "Бюджет, с · 0 = выкл.", "early_risk_seconds", "180", ["0", "120", "180", "240", "300"])
        self.parameter_widgets[-1][0].configure(width=5)
        self.quality_note = ttk.Label(params, style="Muted.TLabel", wraplength=228)
        self.quality_note.pack(anchor="w", pady=(9, 12))
        self.variables['polygons'].trace_add('write', lambda *args: self.apply_quality_dimensions())
        self.apply_quality_dimensions()
        ttk.Checkbutton(params, text="Звук по окончании", variable=self.sound).pack(anchor="w", pady=(0, 12))
        self.generate_button = ttk.Button(actions, text="Запустить очередь", style="Accent.TButton", command=self.generate)
        self.generate_button.pack(fill="x")
        self.cancel_button = ttk.Button(actions, text="Остановить", command=self.cancel, state="disabled")
        self.cancel_button.pack(fill="x", pady=(8, 0))
        self.force_button = ttk.Button(actions, text='Прервать сразу', command=self.force_cancel, state='disabled')
        self.force_button.pack(fill='x', pady=(6, 0))
        self.resume_button = ttk.Button(actions, text="Продолжить задание", command=self.resume)
        if read_json(self.jobs.pending_file).get("prompt_id"):
            self.resume_button.pack(fill="x", pady=(8, 0))
        result = ttk.Frame(body, style="Card.TFrame", padding=18)
        result.grid(row=0, column=2, sticky="nsew")
        self.heading(result, "3. Готовая модель")
        preview_options = ttk.Frame(result, style='Card.TFrame')
        preview_options.pack(fill='x', pady=(8, 0))
        ttk.Label(preview_options, text='Предпросмотр: короткая сторона, px', style='Muted.TLabel').pack(side='left')
        self.preview_selector = ttk.Combobox(preview_options, textvariable=self.preview_size,
                                            values=['128', '256', '512'], width=6, state='readonly')
        self.preview_selector.pack(side='right')
        self.preview_selector.bind('<<ComboboxSelected>>', lambda e: self.change_preview_size())
        self.result_canvas = tk.Canvas(result, bg="#111b28", highlightthickness=0, height=345)
        self.result_canvas.pack(fill="both", expand=True, pady=(15, 10))
        self.placeholder(self.result_canvas, "Здесь появится\nпредпросмотр модели")
        rotate = ttk.Frame(result, style="Card.TFrame")
        rotate.pack(fill="x", pady=(0, 8))
        self.left_button = ttk.Button(rotate, text="↶ Повернуть", command=lambda: self.rotate(-30), state="disabled")
        self.left_button.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.right_button = ttk.Button(rotate, text="Повернуть ↷", command=lambda: self.rotate(30), state="disabled")
        self.right_button.pack(side="left", fill="x", expand=True)
        self.viewer_button = ttk.Button(result,text='Смотреть 3D-модель',command=self.open_model_viewer,state='disabled')
        self.viewer_button.pack(fill='x',pady=(0,8))
        self.low_poly_button = ttk.Button(result,text='Создать low poly копию…',command=self.create_low_poly,state='disabled')
        self.low_poly_button.pack(fill='x',pady=(4,0))
        self.result_label = ttk.Label(result, text="Формат результата: GLB", style="Muted.TLabel", wraplength=335)
        self.result_label.pack(fill="x", pady=(0, 12))
        result_controls = ttk.Frame(result, style="Card.TFrame")
        result_controls.pack(fill="x")
        self.save_button = ttk.Button(result_controls, text="Сохранить GLB…", command=self.save_result, state="disabled")
        self.save_button.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.folder_button = ttk.Button(result_controls, text="Папка модели", command=self.open_folder, state="disabled")
        self.folder_button.pack(side="left", fill="x", expand=True)
        footer = ttk.Frame(root, style="Card.TFrame", padding=(18, 12))
        footer.pack(fill="x", padx=24, pady=(12, 20))
        row = ttk.Frame(footer, style="Card.TFrame")
        row.pack(fill="x")
        ttk.Label(row, textvariable=self.status, wraplength=950).pack(side="left")
        ttk.Label(row, textvariable=self.elapsed, style="Muted.TLabel").pack(side="right")
        self.bar = ttk.Progressbar(footer, maximum=100, mode="determinate")
        self.bar.pack(fill="x", pady=(8, 6))
        details = ttk.Frame(footer, style='Card.TFrame')
        details.pack(fill='x')
        ttk.Label(details, textvariable=self.step_text, style="Muted.TLabel", wraplength=540).pack(side='left')
        ttk.Label(details, textvariable=self.activity_text, style='Muted.TLabel').pack(side='right')
        self.source_canvas.bind("<Configure>", lambda e: self.recenter(self.source_canvas))
        self.result_canvas.bind("<Configure>", lambda e: self.recenter(self.result_canvas))
        saved_image = self.settings.get("image")
        if isinstance(saved_image, dict) and Path(saved_image.get("path") or "").is_file() and Path(saved_image.get("preview") or "").is_file():
            self.image = saved_image
            self.show_source(saved_image)
        self.refresh_queue()
        self.update_controls()
        if poll:
            root.after(100, self.pump)
            if not read_json(self.jobs.pending_file).get('prompt_id'):
                saved_result = self.settings.get('result')
                if not isinstance(saved_result,dict) or not Path(saved_result.get('glb_path','')).is_file():
                    saved_result = next((r for r in recent_runs(self.project) if r.get('status')=='completed'),None)
                if saved_result:
                    root.after(200,lambda:self.restore_result(saved_result))

    def heading(self, parent, text):
        ttk.Label(parent, text=text, font=("Segoe UI", 12, "bold")).pack(anchor="w")

    def parameter(self, parent, label, key, default, choices=None):
        ttk.Label(parent, text=label, style="Muted.TLabel").pack(anchor="w", pady=(6, 2))
        value = str(self.settings.get("parameters", {}).get(key, default))
        if key == "model":
            value = "Pixal3D" if value.lower() == "pixal3d" else "Trellis2"
        if key == 'quality':
            value = next((label for label, identifier in QUALITY_LABELS.items() if identifier == value), value)
        if choices and value not in choices:
            value = default
        var = tk.StringVar(value=value)
        self.variables[key] = var
        widget = ttk.Combobox(parent, textvariable=var, values=choices, state="readonly") if choices else ttk.Entry(parent, textvariable=var)
        widget.pack(fill="x")
        self.parameter_widgets.append((widget, "readonly" if choices else "normal"))

    def placeholder(self, canvas, text):
        canvas.create_text(170, 170, text=text, fill=MUTED, font=("Segoe UI", 13), justify="center", tags="content")

    def recenter(self, canvas):
        if canvas in self.canvas_images:
            photo, key = self.canvas_images[canvas]
            self.fit_photo(canvas, photo, key)
        canvas.coords("content", canvas.winfo_width()/2, canvas.winfo_height()/2)

    def fit_photo(self, canvas, photo, key):
        width, height = canvas.winfo_width(), canvas.winfo_height()
        width = width-8 if width > 8 else 410
        height = height-8 if height > 8 else 410
        factor = max(1, (photo.width()+width-1)//width, (photo.height()+height-1)//height)
        shown = photo.subsample(factor) if factor > 1 else photo
        self.photos[key] = shown
        if canvas.find_withtag('content'):
            canvas.itemconfigure('content', image=shown)

    def show_png(self, canvas, path, key):
        photo = tk.PhotoImage(file=str(path))
        self.canvas_images[canvas] = (photo, key)
        canvas.delete("all")
        canvas.create_image(canvas.winfo_width()/2, canvas.winfo_height()/2, image=photo, tags="content")
        self.fit_photo(canvas, photo, key)
        return photo.width(), photo.height()

    def show_source(self, image):
        self.show_png(self.source_canvas, image["preview"], "source")
        name = Path(image["original"]).name if image["original"] != "Из буфера обмена" else image["original"]
        self.source_label.configure(text=f"{name}\n{image['size'][0]} × {image['size'][1]} px")

    def choose_image(self):
        paths = filedialog.askopenfilenames(parent=self.root, title="Добавить изображения в очередь",
            filetypes=[("Изображения", "*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff"), ("Все файлы", "*.*")])
        self.import_pending.extend(paths)
        self.import_next()

    def import_next(self):
        if self.import_pending and not self.image_loading:
            self.import_image(self.import_pending.pop(0))

    def import_image(self, path=None, clipboard=False):
        if self.image_loading:
            return
        self.image_loading = True
        if not self.jobs.busy:
            self.status.set("Читаю изображение…")
        self.update_controls()
        self.jobs.import_image(path, clipboard=clipboard)

    def paste_image(self):
        self.import_image(clipboard=True)

    def paste_shortcut(self, event):
        if self.root.focus_get() and self.root.focus_get().winfo_class() in {"TEntry", "Entry", "TCombobox", "Spinbox", "TSpinbox"}:
            return
        self.paste_image()
        return "break"

    def parameters(self):
        values = {key: var.get().strip() for key, var in self.variables.items()}
        values["model"] = "pixal3d" if values["model"] == "Pixal3D" else "trellis2"
        values['quality'] = QUALITY_LABELS[values['quality']]
        try:
            params = Parameters(**{k: v if k in ('model', 'quality') else int(v) for k, v in values.items()}, mesh_mode='original')
            params.validate()
            if params.polygons < 4:
                raise ValueError("Полигонов должно быть не меньше 4.")
            return params
        except ValueError:
            raise ValueError("Проверьте параметры: число полигонов — целое от 4; seed — целое число от 0 до 2⁶⁴−1.")

    def apply_quality_dimensions(self):
        if self.variables['quality'].get() == 'Быстро · 512':
            try:
                polygons = int(self.variables['polygons'].get())
            except ValueError:
                return
            if polygons < 4:
                return
            remesh = 128 if polygons <= 1000 else 256
            texture = 128 if polygons <= 1000 else 256 if polygons <= 10000 else 512
            self.variables['remesh_resolution'].set(str(remesh))
            self.variables['texture_size'].set(str(texture))
            self.quality_note.configure(text='Форма 512 · шаги 6 / 8 / 6\nИсходная геометрия, цвет по вершинам.\nБез Remesh и упрощения. Число полигонов — только для отдельной копии.')
        else:
            self.quality_note.configure(text='Детализация 1024 · шаги 12 / 20 / 12\nИсходная геометрия, цвет по вершинам.\nБез Remesh и упрощения.')

    def change_quality(self, event=None):
        if self.jobs.busy:
            return
        if self.variables['quality'].get() == 'Проверенный · 1024':
            for key, value in [('polygons', '10000'), ('remesh_resolution', '256'), ('texture_size', '256')]:
                self.variables[key].set(value)
        self.apply_quality_dimensions()
        self.save_settings()

    def save_settings(self):
        write_json(self.settings_path, {"image": self.image,
                   "parameters": {k: v.get() for k, v in self.variables.items()}, "sound": self.sound.get(),
                   "preview_size": int(self.preview_size.get()),'result':self.result})

    def change_preview_size(self):
        if self.jobs.busy:
            return
        self.jobs.preview_size = int(self.preview_size.get())
        self.save_settings()
        if self.result and not self.preview_loading:
            self.rotate(0)

    def generate(self):
        if self.jobs.busy or self.image_loading or self.checking_images:
            return
        if read_json(self.jobs.pending_file).get('prompt_id') or any(
                item['status'] == 'paused' for item in self.image_queue.items):
            messagebox.showinfo('Очередь', 'Сначала продолжите незавершённое задание.', parent=self.root)
            return
        try:
            self.apply_quality_dimensions()
            self.queue_parameters = self.parameters()
            if not self.image_queue.next() and not self.image_queue.items and self.image and not self.queue_explicit_empty:
                self.image_queue.add(self.image)
            if not self.image_queue.next():
                return
            self.save_settings()
            self.queue_running = True
            self.queue_current = None
            self.advance_queue()
        except Exception as exc:
            self.status.set("Не удалось начать генерацию")
            messagebox.showerror("Параметры генерации", str(exc), parent=self.root)
            self.update_controls()

    def refresh_queue(self):
        selected = self.queue_table.selection()
        self.queue_table.delete(*self.queue_table.get_children())
        states = {'waiting': 'В очереди', 'running': 'Генерация', 'completed': 'Готово',
                  'failed': 'Ошибка', 'paused': 'Продолжить', 'cancelled': 'Остановлено', 'skipped': 'Пропущено · риск'}
        for index, item in enumerate(self.image_queue.items, 1):
            image = item['image']
            name = Path(image.get('original', image['path'])).name
            self.queue_table.insert('', 'end', iid=item['id'],
                values=(f'{index}. {name}', RISK_LABELS.get(item.get('risk', {}).get('level'), '—'),
                        states.get(item['status'], item['status'])), tags=(item.get('risk', {}).get('level', ''),))
        if selected and self.image_queue.get(selected[0]):
            self.queue_table.selection_set(selected[0])
        done = sum(item['status'] == 'completed' for item in self.image_queue.items)
        self.queue_label.configure(text=f'Очередь: готово {done} из {len(self.image_queue.items)}'
                                   + (f' · загружается ещё {len(self.import_pending)}' if self.import_pending else ''))

    def check_queue(self):
        if self.jobs.busy or self.queue_running or self.image_loading or self.checking_images:
            return
        if not self.image_queue.items and self.image and not self.queue_explicit_empty:
            self.image_queue.add(self.image)
        items = [dict(item) for item in self.image_queue.items]
        if not items:
            return
        try:
            parameters = self.parameters().__dict__
        except ValueError as exc:
            messagebox.showerror('Проверка', str(exc), parent=self.root)
            return
        self.checking_images = True
        self.refresh_queue()
        self.status.set(f'Проверяю изображения: {len(items)}. Генерация не запускается.')
        self.update_controls()
        self.jobs.check_images(items, parameters)

    def show_risk_report(self):
        window = tk.Toplevel(self.root)
        window.title('Проверка изображений — без генерации')
        window.geometry('850x540')
        window.configure(bg=BG)
        ttk.Label(window, text='История при тех же настройках + признаки картинки. «Неясно» означает недостаток данных; качество 3D не предсказывается.',
                  style='Muted.TLabel', wraplength=790).pack(fill='x', padx=18, pady=12)
        table = ttk.Treeview(window, columns=('name', 'risk'), show='headings', selectmode='extended', height=8)
        table.heading('name', text='Изображение')
        table.heading('risk', text='Риск')
        table.column('name', width=600)
        table.column('risk', width=100, stretch=False)
        details = tk.StringVar(value='Выберите строку, чтобы увидеть причины. Можно выделить несколько и убрать из очереди.')
        for item in self.image_queue.items:
            risk = item.get('risk')
            if risk:
                table.insert('', 'end', iid=item['id'], values=(Path(item['image'].get('original', item['image']['path'])).name,
                             RISK_LABELS.get(risk['level'], 'Неясно')))
        table.pack(fill='both', expand=True, padx=18)
        def select(event=None):
            selected = table.selection()
            item = self.image_queue.get(selected[0]) if selected else None
            if item:
                details.set('\n'.join('• ' + reason for reason in item['risk']['reasons']))
        table.bind('<<TreeviewSelect>>', select)
        ttk.Label(window, textvariable=details, wraplength=790).pack(fill='x', padx=18, pady=12)
        def remove():
            for item_id in table.selection():
                item = self.image_queue.get(item_id)
                if item and item['status'] not in ('running', 'paused'):
                    self.image_queue.remove(item_id)
                    table.delete(item_id)
            if not self.image_queue.items:
                self.queue_explicit_empty = True
            self.refresh_queue()
            self.update_controls()
            details.set('Выбранные строки убраны из очереди. Файлы изображений и моделей сохранены.')
        ttk.Button(window, text='Убрать выбранные из очереди', command=remove).pack(side='left', padx=18, pady=12)
        ttk.Button(window, text='Закрыть', command=window.destroy).pack(side='right', padx=18, pady=12)

    def move_queue_item(self, offset):
        selection = self.queue_table.selection()
        if selection:
            self.image_queue.move(selection[0], offset)
            self.refresh_queue()

    def remove_queue_item(self):
        for item_id in self.queue_table.selection():
            item = self.image_queue.get(item_id)
            if item and item['status'] in ('running', 'paused'):
                self.force_cancel()
                return
            self.image_queue.remove(item_id)
        if not self.image_queue.items:
            self.queue_explicit_empty = True
        self.refresh_queue()
        self.update_controls()

    def clear_finished_queue(self):
        for item in list(self.image_queue.items):
            if item['status'] in ('completed', 'failed', 'cancelled'):
                self.image_queue.remove(item['id'])
        if not self.image_queue.items:
            self.queue_explicit_empty = True
        self.refresh_queue()
        self.update_controls()

    def open_queue_item(self, event=None):
        if self.jobs.busy or self.preview_loading:
            return
        selected = self.queue_table.selection()
        item = self.image_queue.get(selected[0]) if selected else None
        if item:
            self.image = item['image']
            self.show_source(self.image)
            if item.get('result'):
                self.on_result(item['result'], notify=False)
                self.preview_loading = True
                self.jobs.preview(item['result']['glb_path'])
            self.update_controls()

    def advance_queue(self):
        if not self.queue_running or self.jobs.busy or self.image_loading:
            return
        item = self.image_queue.next()
        if item is None:
            if self.import_pending:
                return
            self.queue_running = False
            self.queue_current = None
            done = sum(item['status'] == 'completed' for item in self.image_queue.items)
            skipped = sum(item['status'] == 'skipped' for item in self.image_queue.items)
            total = len(self.image_queue.items)
            self.status.set(f'Очередь завершена. Готово {done} из {total}. Пропущено: {skipped}.')
            self.refresh_queue()
            self.update_controls()
            if self.sound.get() and total and all(i['status'] in ('completed', 'skipped', 'failed', 'cancelled')
                                                 for i in self.image_queue.items):
                self.notify_completion()
            return
        self.queue_current = item['id']
        self.image_queue.mark(item['id'], 'running')
        self.refresh_queue()
        self.queue_table.selection_set(item['id'])
        self.queue_table.see(item['id'])
        try:
            self.image = item['image']
            self.show_source(self.image)
            self.begin()
            self.jobs.start(self.image['path'], self.queue_parameters, preview_size=int(self.preview_size.get()))
        except Exception as exc:
            self.image_queue.mark(item['id'], 'failed', error=str(exc))
            self.queue_running = False
            self.queue_current = None
            self.refresh_queue()
            self.status.set('Очередь приостановлена: не удалось начать задание')
            messagebox.showerror('Очередь', str(exc), parent=self.root)
        self.update_controls()

    def begin(self):
        self.started = time.monotonic()
        self.current_node = None
        self.stage_started = self.started
        self.last_progress_at = self.started
        self.last_progress_clock = time.strftime('%H:%M:%S')
        self.phase_key = 'preparing'
        self.final_ready = False
        self.intermediate_shown = False
        self.result = None
        self.result_canvas.delete('all')
        self.canvas_images.pop(self.result_canvas, None)
        self.photos.pop('result', None)
        self.placeholder(self.result_canvas, 'Модель появится после завершения генерации')
        self.recenter(self.result_canvas)
        self.result_label.configure(text='Промежуточный предпросмотр отключён')
        self.bar.stop()
        self.bar.configure(mode="indeterminate")
        self.bar.start(16)
        self.status.set("Подготавливаю генерацию…")
        self.step_text.set("Сервер запускается автоматически. Окно можно оставить открытым.")

    def resume(self):
        pending = read_json(self.jobs.pending_file)
        if self.jobs.busy or not pending.get("prompt_id"):
            return
        try:
            params = Parameters(**pending.get("parameters", {"model": "trellis2"}))
            self.queue_parameters = params
            item = next((item for item in self.image_queue.items
                         if item['image']['path'] == pending.get('image') and item['status'] == 'paused'), None)
            if item:
                self.queue_current = item['id']
                self.queue_running = True
                self.image_queue.mark(item['id'], 'running')
                self.refresh_queue()
            self.begin()
            self.jobs.start(pending.get("image", ""), params, prompt_id=pending["prompt_id"],
                            preview_size=pending.get('preview_size', int(self.preview_size.get())))
            self.update_controls()
        except Exception as exc:
            self.queue_running = False
            if self.queue_current:
                self.image_queue.mark(self.queue_current, 'paused', error=str(exc))
                self.refresh_queue()
            messagebox.showerror("Продолжение задания", str(exc), parent=self.root)
            self.update_controls()

    def cancel(self):
        if self.low_poly_running:
            self.jobs.cancel_copy()
            self.status.set('Останавливаю создание копии…')
            return
        if messagebox.askyesno("Остановить генерацию?", "Текущая генерация будет остановлена. Остальные картинки останутся в очереди.", parent=self.root):
            self.queue_running = False
            self.cancel_button.configure(state="disabled")
            self.status.set("Отправляю запрос остановки…")
            self.jobs.cancel()

    def force_cancel(self):
        if self.jobs.force_stopping:
            return
        if messagebox.askyesno('Прервать сразу?',
                'ComfyUI будет принудительно завершён — все его текущие задания остановятся.\n'
                'Текущая картинка будет убрана из очереди. Остальные картинки и готовые файлы останутся.\n\n'
                'При следующей генерации ComfyUI запустится заново. Прервать?', parent=self.root):
            if self.queue_current is None:
                selected = self.queue_table.selection()
                item = self.image_queue.get(selected[0]) if selected else None
                if item and item['status'] in ('running', 'paused'):
                    self.queue_current = item['id']
            self.queue_running = False
            self.jobs.force_cancel()
            self.update_controls()

    def on_progress(self, event):
        status = event.get("status")
        if status != "generating":
            if status in STAGES:
                self.touch_progress('status:'+status)
                self.status.set(STAGES[status])
                if status == 'retrying_structure':
                    self.status.set(f"Повтор структуры · seed {event.get('seed')}")
                    self.step_text.set(event.get('reason', ''))
            self.update_controls()
            return
        data = event.get("event", {})
        node = str(data.get("node", ""))
        if data.get("type") == "executing":
            self.touch_progress('node:'+node)
            self.current_node = node
            self.status.set(self.generation_node_name(node))
            self.bar.stop()
            self.bar.configure(mode="indeterminate")
            self.bar.start(16)
            self.step_text.set("Выполняется текущий этап…")
        elif data.get("type") == "progress":
            done, total = data.get("completed"), data.get("total")
            if not isinstance(done, (int, float)) or not isinstance(total, (int, float)) or total <= 0:
                return
            if self.current_node and node != self.current_node:
                return
            self.touch_progress('node:'+node)
            self.current_node = node
            percentage = max(0, min(100, 100*done/total))
            self.bar.stop()
            self.bar.configure(mode="determinate", value=percentage)
            self.status.set(self.generation_node_name(node))
            self.step_text.set(f"Текущий этап: {done:g} / {total:g} — {percentage:.0f}%")

    def generation_node_name(self, node):
        if node in NODE_NAMES:
            return NODE_NAMES[node]
        if self.jobs.metadata_path:
            meta = read_json(Path(self.jobs.metadata_path))
            budget_node = meta.get('build', {}).get('summary', {}).get('structure_budget_node')
            if budget_node is not None and str(budget_node) == node:
                return 'Проверка сложности · повтор структуры'
        return 'Обработка модели'

    def touch_progress(self, phase):
        now = time.monotonic()
        if self.phase_key != phase:
            self.phase_key, self.stage_started = phase, now
        self.last_progress_at = now
        self.last_progress_clock = time.strftime('%H:%M:%S')

    def on_result(self, result, *, notify=True):
        self.result = result
        self.final_ready = True
        self.intermediate_shown = False
        self.yaw = 45
        self.result_label.configure(text=Path(result["glb_path"]).name + "\nGLB сохранён")
        self.status.set("3D-модель готова и сохранена")
        self.bar.stop()
        self.bar.configure(mode="determinate", value=100)
        self.step_text.set("Готово. Можно сохранить GLB в нужную папку.")
        self.activity_text.set('GLB сохранён: '+time.strftime('%H:%M:%S'))
        self.update_controls()
        self.save_settings()
        if notify and self.sound.get():
            self.notify_completion()

    def notify_completion(self):
        def beep():
            try:
                completion_sound()
            except (OSError, RuntimeError):
                pass
        threading.Thread(target=beep, daemon=True).start()

    def handle_event(self, event):
        if event.get('job_token') and event['job_token'] != self.jobs.run_token:
            return
        kind = event["kind"]
        if kind == 'force_stopping':
            self.status.set('Принудительно завершаю ComfyUI…')
        elif kind == 'force_cancelled':
            self.queue_running = False
            if self.queue_current:
                self.image_queue.mark(self.queue_current, 'cancelled')
                self.image_queue.remove(self.queue_current)
            self.queue_current = None
            self.preview_loading = False
            self.result = None
            self.final_ready = False
            self.bar.stop()
            self.bar.configure(mode='determinate', value=0)
            self.result_canvas.delete('all')
            self.canvas_images.pop(self.result_canvas, None)
            self.photos.pop('result', None)
            self.placeholder(self.result_canvas, 'Генерация прервана')
            self.recenter(self.result_canvas)
            self.status.set('Генерация прервана. Можно запустить оставшуюся очередь.')
            self.step_text.set('ComfyUI запустится заново при следующей генерации.')
            self.refresh_queue()
        elif kind == 'force_cancel_error':
            self.status.set('Не удалось принудительно завершить ComfyUI')
            messagebox.showerror('Принудительная остановка', event['message'], parent=self.root)
        elif kind == 'image_risk':
            item = self.image_queue.get(event['item_id'])
            if item:
                if not (item['status']=='skipped' and item.get('risk',{}).get('source')=='runtime'):
                    self.image_queue.mark(item['id'], item['status'], risk=event['risk'])
                self.refresh_queue()
        elif kind == 'risk_check_done':
            self.checking_images = False
            self.status.set('Проверка закончена. Уберите нежелательные картинки и запустите очередь.')
            self.show_risk_report()
        elif kind == "image":
            self.image_loading = False
            self.image_queue.add(event['image'])
            self.queue_explicit_empty = False
            if not self.jobs.busy:
                self.image = event["image"]
                self.show_source(self.image)
                self.status.set("Картинка добавлена. Нажмите «Запустить очередь»")
            self.refresh_queue()
            self.save_settings()
            self.import_next()
            if self.queue_current is None:
                self.advance_queue()
        elif kind == "image_error":
            self.image_loading = False
            self.status.set("Не удалось прочитать изображение")
            messagebox.showerror("Изображение", event["message"], parent=self.root)
            self.import_next()
            if self.queue_current is None:
                self.advance_queue()
        elif kind == "progress":
            self.on_progress(event["event"])
        elif kind == "result":
            if self.queue_current:
                self.image_queue.mark(self.queue_current, 'completed', result=event['result'])
                self.refresh_queue()
            self.on_result(event["result"], notify=not bool(self.queue_current))
        elif kind == 'idle':
            self.low_poly_running = False
            self.queue_current = None
            self.advance_queue()
        elif kind == 'low_poly_result':
            self.bar.stop()
            self.bar.configure(mode='determinate',value=100)
            self.status.set('Low poly копия готова. Основная модель сохранена без изменений.')
            self.step_text.set(f"Копия: {event['result']['triangles']:,} треугольников".replace(',',' '))
            from .viewer import ModelViewer
            try:
                if self.model_viewer and not self.model_viewer.closed:
                    self.model_viewer.close()
                self.model_viewer = ModelViewer(self.root,event['result']['glb_path'],self.project)
            except OSError as exc:
                messagebox.showinfo('Low poly копия',f"Файл сохранён: {event['result']['glb_path']}\n{exc}",parent=self.root)
        elif kind == 'low_poly_error':
            self.bar.stop()
            self.status.set('Low poly копия не создана. Оригинал не изменён.')
            self.step_text.set(event['message'])
            messagebox.showinfo('Low poly копия',event['message'],parent=self.root)
        elif kind == 'low_poly_cancelled':
            self.bar.stop()
            self.status.set('Создание копии отменено. Оригинал не изменён.')
        elif kind == 'intermediate_preview':
            if event.get('token') != self.jobs.run_token or self.final_ready:
                return
            snapshot = event['snapshot']
            try:
                width, height = self.show_png(self.result_canvas, snapshot['image'], 'result')
            except (tk.TclError, OSError):
                # A replaced/removed milestone image must not stop the UI pump.
                return
            self.intermediate_shown = True
            label = PREVIEW_STAGES.get(snapshot['stage'], 'Промежуточный результат')
            triangles = snapshot.get('triangles')
            if isinstance(triangles, int):
                label += f" · {triangles:,} треугольников".replace(',', ' ')
            self.result_label.configure(text=label+f"\nПредпросмотр {width}×{height}")
        elif kind == "preview":
            self.preview_loading = False
            if self.result and event["glb"] == self.result["glb_path"]:
                width, height = self.show_png(self.result_canvas, event["preview"], "result")
                report = read_json(Path(event['preview']).with_suffix('.json'))
                triangles = report.get('sourceTriangles')
                count = f" · {triangles:,} треугольников".replace(',',' ') if isinstance(triangles,int) else ''
                self.result_label.configure(text=Path(self.result['glb_path']).name+f"\nGLB сохранён · вид {width}×{height}"+count)
                self.status.set("3D-модель готова")
                self.bar.stop()
                self.bar.configure(mode="determinate", value=100)
                self.step_text.set("GLB сохранён. Предпросмотр исходной геометрии и цвета.")
        elif kind == "preview_error":
            self.preview_loading = False
            self.status.set("GLB готов; предпросмотр недоступен")
            self.step_text.set(event["message"])
        elif kind == 'risk_skipped':
            if self.queue_current:
                self.image_queue.mark(self.queue_current, 'skipped', error=event['message'],
                    risk={'version': RISK_VERSION, 'level': 'high', 'source': 'runtime', 'reasons': [event['message']]})
                self.refresh_queue()
            self.status.set('Высокий риск · картинка пропущена')
            self.step_text.set(event['message'])
            self.bar.stop()
            self.bar.configure(mode='determinate', value=0)
        elif kind == "error":
            if self.queue_current:
                self.image_queue.mark(self.queue_current, 'paused' if event.get('resumable') else
                                      'cancelled' if event.get('cancelled') else 'failed', error=event['message'])
                self.queue_running = False
                self.refresh_queue()
            self.bar.stop()
            self.bar.configure(mode="determinate", value=0)
            self.status.set("Генерация остановлена" if event.get("cancelled") else "Ошибка генерации")
            self.step_text.set(event["message"])
            if not event.get("cancelled"):
                details = event["message"]
                if event.get("metadata_path"):
                    details += "\n\nЖурнал: " + event["metadata_path"]
                messagebox.showerror("Генерация 3D", details, parent=self.root)
        elif kind == "cancel_requested":
            self.status.set("Останавливаю после текущего вычисления…")
        elif kind == "cancel_error":
            messagebox.showerror("Остановка", event["message"], parent=self.root)
        self.update_controls()

    def pump(self):
        try:
            for _ in range(120):
                self.handle_event(self.jobs.events.get_nowait())
        except queue.Empty:
            pass
        if self.started and self.jobs.busy:
            now = time.monotonic()
            self.elapsed.set('Всего '+duration(now-self.started))
            if self.stage_started and self.last_progress_at and not self.final_ready:
                self.activity_text.set(f"Этап {duration(now-self.stage_started)} · Обновление {self.last_progress_clock} "
                                       f"({int(now-self.last_progress_at)} с назад)")
        self.root.after(100, self.pump)

    def update_controls(self):
        available = not self.jobs.busy and not self.image_loading and not self.preview_loading and not self.checking_images
        self.check_button.configure(state='normal' if available and not self.queue_running and
                                    (self.image_queue.items or (self.image and not self.image_queue.items and
                                     not self.queue_explicit_empty)) else 'disabled')
        self.preview_selector.configure(state='readonly' if available else 'disabled')
        self.generate_button.configure(state="normal" if available and not self.queue_running and
                                       (self.image_queue.next() or (self.image and not self.image_queue.items and
                                        not self.queue_explicit_empty)) else "disabled")
        for button in (self.choose_button, self.paste_button):
            button.configure(state="normal" if not self.image_loading and not self.checking_images else "disabled")
        for widget, ready_state in self.parameter_widgets:
            widget.configure(state=ready_state if not self.jobs.busy else "disabled")
        self.cancel_button.configure(state="normal" if self.jobs.busy and self.jobs.prompt_id and not self.jobs.cancel_requested else "disabled")
        if self.low_poly_running:
            self.cancel_button.configure(state='normal' if not self.jobs.cancel_requested else 'disabled')
        self.force_button.configure(state='normal' if (self.jobs.busy or read_json(self.jobs.pending_file).get('prompt_id'))
                                    and not self.jobs.force_stopping and not self.low_poly_running else 'disabled')
        for button in (self.save_button, self.folder_button):
            button.configure(state="normal" if self.result and not self.intermediate_shown and
                             (not self.jobs.busy or self.final_ready) else "disabled")
        for button in (self.left_button, self.right_button):
            button.configure(state="normal" if self.result and not self.jobs.busy and not self.preview_loading else "disabled")
        self.viewer_button.configure(state='normal' if self.result and self.final_ready and not self.jobs.busy else 'disabled')
        self.low_poly_button.configure(state='normal' if self.result and self.final_ready and available and not self.queue_running else 'disabled')
        if not self.jobs.busy and read_json(self.jobs.pending_file).get("prompt_id"):
            self.resume_button.pack(fill="x", pady=(8, 0))
        else:
            self.resume_button.pack_forget()

    def rotate(self, delta):
        if self.result and not self.preview_loading:
            self.yaw += delta
            self.preview_loading = True
            self.status.set("Поворачиваю предпросмотр…")
            self.update_controls()
            self.jobs.preview(self.result["glb_path"], self.yaw)

    def open_model_viewer(self):
        if self.result and self.final_ready and not self.jobs.busy:
            from .viewer import ModelViewer
            if self.model_viewer and not self.model_viewer.closed:
                if self.model_viewer.source == Path(self.result['glb_path']).resolve():
                    self.model_viewer.focus()
                    return
                self.model_viewer.close()
            try:
                self.model_viewer = ModelViewer(self.root,self.result['glb_path'],self.project)
            except OSError as error:
                messagebox.showerror('Просмотр 3D',str(error),parent=self.root)

    def create_low_poly(self):
        if not self.result or self.jobs.busy or self.queue_running:
            return
        try:
            polygons = int(self.variables['polygons'].get())
            if polygons<4:
                raise ValueError('Полигонов должно быть не меньше 4')
            self.jobs.simplify(self.result['glb_path'],polygons)
            self.low_poly_running = True
            self.status.set('Создаю отдельную low poly копию на CPU…')
            self.step_text.set('Исходная модель остаётся основным результатом.')
            self.bar.configure(mode='indeterminate')
            self.bar.start(12)
            self.update_controls()
        except Exception as exc:
            messagebox.showerror('Low poly копия',str(exc),parent=self.root)

    def restore_result(self,record):
        if self.started or self.jobs.busy or self.result:
            return
        self.on_result(record,notify=False)
        preview = Path(record['glb_path']).with_suffix('.preview.png')
        if preview.exists():
            self.handle_event(dict(kind='preview',preview=str(preview),glb=record['glb_path']))
        else:
            self.preview_loading = True
            self.jobs.preview(record['glb_path'])

    def save_result(self):
        if not self.result:
            return
        source = Path(self.result["glb_path"])
        destination = filedialog.asksaveasfilename(parent=self.root, title="Сохранить 3D-модель",
            initialfile="model.glb", defaultextension=".glb", filetypes=[("3D-модель GLB", "*.glb")])
        if destination:
            try:
                if Path(destination).resolve() != source.resolve():
                    shutil.copy2(source, destination)
                self.status.set("Модель сохранена: " + destination)
            except OSError as exc:
                messagebox.showerror("Сохранение GLB", str(exc), parent=self.root)

    def open_folder(self):
        if self.result:
            os.startfile(str(Path(self.result["glb_path"]).parent))

    def history(self):
        if self.jobs.busy or self.preview_loading:
            return
        window = tk.Toplevel(self.root)
        window.title("Dart3s — История моделей")
        window.geometry("740x430")
        window.configure(bg=BG)
        rows = recent_runs(self.project)
        table = ttk.Treeview(window, columns=("date", "model", "status"), show="headings")
        table.heading("date", text="Создано")
        table.heading("model", text="Модель")
        table.heading("status", text="Статус")
        states = {"completed": "Готово", "submitted": "Ожидает результата", "failed": "Ошибка", "missing": "Файл отсутствует"}
        for i, record in enumerate(rows):
            date = record.get("started_at", "")
            try:
                from datetime import datetime
                date = datetime.fromisoformat(date).astimezone().strftime("%d.%m.%Y %H:%M")
            except ValueError:
                pass
            table.insert("", "end", iid=str(i), values=(date, record.get("parameters", {}).get("model", "—"),
                                                       states.get(record.get("status"), record.get("status", "—"))))
        table.pack(fill="both", expand=True, padx=18, pady=18)
        def choose():
            selected = table.selection()
            if not selected:
                return
            record = rows[int(selected[0])]
            if record.get("status") == "completed":
                self.on_result(record, notify=False)
                preview = Path(record["glb_path"]).with_suffix(".preview.png")
                if preview.exists():
                    self.handle_event({"kind": "preview", "preview": str(preview), "glb": record["glb_path"]})
                else:
                    self.preview_loading = True
                    self.jobs.preview(record["glb_path"])
                window.destroy()
            elif record.get("prompt_id") and record.get("status") == "submitted":
                write_json(self.jobs.pending_file, record)
                window.destroy()
                self.resume()
        ttk.Button(window, text="Открыть / продолжить", command=choose).pack(pady=(0, 18))
        table.bind("<Double-1>", lambda e: choose())

    def close(self):
        if getattr(self, 'shutdown_thread', None) is not None:
            return
        self.queue_running = False
        for item in self.image_queue.items:
            if item['status'] in ('running', 'paused'):
                item['status'] = 'waiting'
        self.image_queue.save()
        self.save_settings()
        self.shutdown_thread = self.jobs.shutdown()
        self.root.destroy()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument('--verify-boat', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--observe-prompt', help=argparse.SUPPRESS)
    parser.add_argument('--observe-metadata', help=argparse.SUPPRESS)
    args = parser.parse_args()
    mutex = None
    if os.name == "nt" and not args.smoke_test:
        import ctypes
        import hashlib
        native = ctypes.WinDLL("kernel32", use_last_error=True)
        native.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        native.CreateMutexW.restype = ctypes.c_void_p
        mutex = native.CreateMutexW(None, False, "Local\\Dart3sUI-" + hashlib.sha256(str(PROJECT).encode()).hexdigest()[:16])
        if ctypes.get_last_error() == 183:
            user32 = ctypes.windll.user32
            user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
            user32.FindWindowW.restype = ctypes.c_void_p
            handle = user32.FindWindowW(None, "Dart3s — Генерация 3D")
            if handle:
                user32.ShowWindow(ctypes.c_void_p(handle), 9)
                user32.SetForegroundWindow(ctypes.c_void_p(handle))
            native.CloseHandle(ctypes.c_void_p(mutex))
            return
    root = tk.Tk()
    if args.smoke_test:
        root.withdraw()
    app = App(root, poll=not args.smoke_test)
    if args.verify_boat and not args.smoke_test:
        from .boat_verification import configure
        configure(app, prompt_id=args.observe_prompt, metadata_path=args.observe_metadata)
    if args.smoke_test:
        root.update_idletasks()
        print(json.dumps({"ok": True, "title": root.title(), "parameters": app.parameters().__dict__,
                          "source_button": str(app.choose_button["state"]),
                          "generate_without_image": str(app.generate_button["state"])}, ensure_ascii=False))
        root.destroy()
        return
    try:
        root.mainloop()
    finally:
        shutdown = getattr(app, 'shutdown_thread', None)
        if shutdown is not None:
            shutdown.join(20)
        if mutex:
            native.CloseHandle(ctypes.c_void_p(mutex))


if __name__ == "__main__":
    main()
