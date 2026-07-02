#!/usr/bin/env python3
"""
Analizador de Calidad de Imágenes
Un script autónomo con interfaz gráfica (Tkinter) para evaluar la calidad
de una o varias imágenes antes de procesarlas para PDF.
"""

import os
import sys
import time
import queue
import threading
import subprocess
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# Intentar importar librerías críticas para el análisis
try:
    import cv2
    import numpy as np
    from PIL import Image
except ImportError as e:
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror(
        "Faltan Dependencias",
        f"No se pudieron importar las dependencias necesarias. Por favor ejecuta:\n"
        f"uv pip install pillow opencv-python numpy\n\nDetalle: {e}"
    )
    sys.exit(1)

# Dimensiones estándar de cartas (en milímetros y pulgadas)
CARD_W_MM = 63.5
CARD_H_MM = 88.9
CARD_W_IN = 2.5
CARD_H_IN = 3.5

# Sangrado de MPC (de cropper.py)
BLEED_X_FRAC = 0.042
BLEED_Y_FRAC = 0.031

# Colores del tema oscuro premium
BG_DARK = "#1e1e1e"
BG_PANEL = "#2d2d2d"
BG_HIGHLIGHT = "#3a3a3a"
TEXT_WHITE = "#ffffff"
TEXT_MUTED = "#a0a0a0"
COLOR_PRIMARY = "#007acc"
COLOR_PRIMARY_HOVER = "#0096ff"
COLOR_SUCCESS = "#4caf50"
COLOR_SUCCESS_HOVER = "#66bb6a"
COLOR_WARNING = "#ff9800"
COLOR_DANGER = "#d32f2f"
COLOR_DANGER_HOVER = "#f44336"


class HoverButton(tk.Button):
    """Un botón plano con efectos de hover para una estética moderna."""
    def __init__(self, master, bg_color, fg_color, hover_bg, hover_fg, **kw):
        super().__init__(
            master,
            bg=bg_color,
            fg=fg_color,
            activebackground=hover_bg,
            activeforeground=hover_fg,
            relief="flat",
            bd=0,
            highlightbackground=bg_color,
            highlightthickness=0,
            padx=12,
            pady=6,
            cursor="hand2",
            **kw
        )
        self.bg_color = bg_color
        self.fg_color = fg_color
        self.hover_bg = hover_bg
        self.hover_fg = hover_fg
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)

    def _on_enter(self, event):
        self.config(bg=self.hover_bg, fg=self.hover_fg, highlightbackground=self.hover_bg)

    def _on_leave(self, event):
        self.config(bg=self.bg_color, fg=self.fg_color, highlightbackground=self.bg_color)


def analyze_single_image(file_path: Path) -> dict:
    """
    Analiza una sola imagen y calcula las 5 métricas de calidad.
    Retorna un diccionario con los resultados formateados.
    """
    res = {
        "filepath": str(file_path),
        "filename": file_path.name,
        "integrity": "OK",
        "resolution": "N/A",
        "dpi_est": "N/A",
        "sharpness": "N/A",
        "brightness": "N/A",
        "contrast": "N/A",
        "aspect": "N/A",
        "is_ok": True,
    }

    try:
        # 1. Integridad: Verificar apertura y decodificación completa
        try:
            with Image.open(file_path) as img:
                img.load()  # Decodifica la imagen para comprobar integridad real
                w, h = img.size
        except Exception as e:
            res["integrity"] = f"Corrupto: {e}"
            res["is_ok"] = False
            return res

        # 2. Resolución y DPI Estimado
        res["resolution"] = f"{w}x{h}"
        
        # Ancho útil estimado recortando sangrado MPC
        w_cropped = w * (1.0 - 2.0 * BLEED_X_FRAC)
        dpi_cropped = round(w_cropped / CARD_W_IN)
        dpi_raw = round(w / CARD_W_IN)
        
        # Clasificación del DPI
        if dpi_cropped >= 300:
            dpi_label = "Alta"
        elif dpi_cropped >= 200:
            dpi_label = "Media"
        else:
            dpi_label = "Baja"
            res["is_ok"] = False
            
        res["dpi_est"] = f"{dpi_cropped} ({dpi_label})"

        # 3. Proporción (Aspect Ratio)
        ratio = w / h
        # Proporción teórica con sangrado MPC: ~0.731
        # Proporción teórica sin sangrado: ~0.714
        # Rango aceptable para cartas estándar: 0.68 - 0.77
        if 0.68 <= ratio <= 0.77:
            res["aspect"] = f"{ratio:.3f} (Correcto)"
        else:
            res["aspect"] = f"{ratio:.3f} (Incorrecto)"
            res["is_ok"] = False

        # Convertir a numpy / OpenCV para análisis de nitidez, brillo y contraste
        img_cv = cv2.imread(str(file_path))
        if img_cv is None:
            res["integrity"] = "Error OpenCV: No se pudo decodificar"
            res["is_ok"] = False
            return res

        gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)

        # 4. Nitidez (Varianza del Laplaciano)
        lap_var = cv2.Laplacian(gray, cv2.CV_64F).var()
        if lap_var >= 300:
            sharp_label = "Alta (Nítido)"
        elif lap_var >= 100:
            sharp_label = "Media (Aceptable)"
        else:
            sharp_label = "Baja (Borroso)"
            res["is_ok"] = False
        res["sharpness"] = f"{lap_var:.1f} ({sharp_label})"

        # 5. Brillo y Contraste
        mean_val = np.mean(gray)
        std_val = np.std(gray)

        # Clasificación de brillo
        if mean_val < 50:
            bright_label = "Muy Oscuro"
            res["is_ok"] = False
        elif mean_val > 200:
            bright_label = "Muy Claro"
        else:
            bright_label = "Normal"
        res["brightness"] = f"{mean_val:.1f} ({bright_label})"

        # Clasificación de contraste
        if std_val < 30:
            contrast_label = "Bajo"
            res["is_ok"] = False
        else:
            contrast_label = "Normal"
        res["contrast"] = f"{std_val:.1f} ({contrast_label})"

    except Exception as e:
        res["integrity"] = f"Error inesperado: {e}"
        res["is_ok"] = False

    return res


class AnalyzerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Analizador de Calidad de Imágenes — MPCFillToPDF Utility")
        self.root.geometry("1000x650")
        self.root.configure(bg=BG_DARK)

        # Habilitar soporte de alta densidad (DPI)
        try:
            self.root.tk.call('tk', 'scaling', 1.5)
        except Exception:
            pass

        # Rutas cargadas pendientes de procesar
        self.pending_files = []
        
        # Cola de comunicación entre hilos
        self.queue = queue.Queue()
        self.is_processing = False

        self._build_style()
        self._build_ui()

    def _build_style(self):
        style = ttk.Style()
        style.theme_use("clam")

        # Configurar colores del Notebook
        style.configure(
            "TNotebook",
            background=BG_DARK,
            borderwidth=0,
            highlightthickness=0
        )
        style.configure(
            "TNotebook.Tab",
            background=BG_PANEL,
            foreground=TEXT_MUTED,
            font=("Arial", 10, "bold"),
            padding=[15, 6],
            relief="flat"
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", BG_DARK)],
            foreground=[("selected", TEXT_WHITE)]
        )

        # Configurar estilo de Treeview
        style.configure(
            "Treeview",
            background=BG_PANEL,
            foreground=TEXT_WHITE,
            fieldbackground=BG_PANEL,
            rowheight=28,
            font=("Arial", 9),
            borderwidth=0
        )
        style.configure(
            "Treeview.Heading",
            background=BG_HIGHLIGHT,
            foreground=TEXT_WHITE,
            font=("Arial", 9, "bold"),
            relief="flat"
        )
        style.map(
            "Treeview",
            background=[("selected", "#005999")],
            foreground=[("selected", TEXT_WHITE)]
        )
        style.map(
            "Treeview.Heading",
            background=[("active", "#4d4d4d")]
        )

        # Barra de progreso
        style.configure(
            "TProgressbar",
            troughcolor=BG_PANEL,
            background=COLOR_PRIMARY,
            thickness=14
        )

        # Scrollbars
        style.configure(
            "Vertical.TScrollbar",
            gripcount=0,
            background=BG_HIGHLIGHT,
            troughcolor=BG_DARK,
            bordercolor=BG_DARK,
            arrowcolor=TEXT_WHITE
        )
        style.configure(
            "Horizontal.TScrollbar",
            gripcount=0,
            background=BG_HIGHLIGHT,
            troughcolor=BG_DARK,
            bordercolor=BG_DARK,
            arrowcolor=TEXT_WHITE
        )

    def _build_ui(self):
        # Contenedor Principal
        main_container = tk.Frame(self.root, bg=BG_DARK)
        main_container.pack(fill="both", expand=True, padx=15, pady=15)

        # Encabezado (Header)
        header_frame = tk.Frame(main_container, bg=BG_DARK)
        header_frame.pack(fill="x", pady=(0, 15))

        title_label = tk.Label(
            header_frame,
            text="Analizador de Calidad de Imágenes",
            font=("Arial", 16, "bold"),
            bg=BG_DARK,
            fg=TEXT_WHITE
        )
        title_label.pack(side="left")

        subtitle_label = tk.Label(
            header_frame,
            text="Métricas: Resolución, DPI, Nitidez, Brillo, Contraste y Aspecto",
            font=("Arial", 9, "italic"),
            bg=BG_DARK,
            fg=TEXT_MUTED
        )
        subtitle_label.pack(side="left", padx=15, pady=(5, 0))

        # Panel de Pestañas (Notebook)
        self.notebook = ttk.Notebook(main_container)
        self.notebook.pack(fill="both", expand=True)

        # Pestaña 1: Configuración / Archivos
        self.tab_files = tk.Frame(self.notebook, bg=BG_DARK)
        self.notebook.add(self.tab_files, text=" 1. Selección de Imágenes ")

        # Pestaña 2: Resultados
        self.tab_results = tk.Frame(self.notebook, bg=BG_DARK)
        self.notebook.add(self.tab_results, text=" 2. Resultados del Análisis ")

        self._build_tab_files()
        self._build_tab_results()

        # Barra de Estado Inferior
        self.status_frame = tk.Frame(main_container, bg=BG_DARK)
        self.status_frame.pack(fill="x", pady=(10, 0))

        self.status_label = tk.Label(
            self.status_frame,
            text="Listo para añadir imágenes.",
            font=("Arial", 9),
            bg=BG_DARK,
            fg=TEXT_MUTED
        )
        self.status_label.pack(side="left")

        self.progress = ttk.Progressbar(
            self.status_frame,
            orient="horizontal",
            mode="determinate",
            style="TProgressbar"
        )
        self.progress.pack(side="right", fill="x", expand=True, padx=(20, 0))

    def _build_tab_files(self):
        # Frame interior con bordes/padding
        inner_frame = tk.Frame(self.tab_files, bg=BG_DARK)
        inner_frame.pack(fill="both", expand=True, pady=10)

        # Panel izquierdo de Controles
        controls_frame = tk.Frame(inner_frame, bg=BG_PANEL, padx=15, pady=15)
        controls_frame.pack(side="left", fill="y", padx=(0, 10))

        # Título sección controls
        actions_label = tk.Label(
            controls_frame,
            text="ACCIONES",
            font=("Arial", 10, "bold"),
            bg=BG_PANEL,
            fg=TEXT_WHITE
        )
        actions_label.pack(anchor="w", pady=(0, 15))

        # Botones de control
        btn_add_files = HoverButton(
            controls_frame,
            bg_color=COLOR_PRIMARY,
            fg_color=TEXT_WHITE,
            hover_bg=COLOR_PRIMARY_HOVER,
            hover_fg=TEXT_WHITE,
            text="Añadir Imágenes...",
            font=("Arial", 10, "bold"),
            width=18,
            command=self._add_files
        )
        btn_add_files.pack(pady=5)

        btn_add_dir = HoverButton(
            controls_frame,
            bg_color=COLOR_PRIMARY,
            fg_color=TEXT_WHITE,
            hover_bg=COLOR_PRIMARY_HOVER,
            hover_fg=TEXT_WHITE,
            text="Añadir Carpeta...",
            font=("Arial", 10, "bold"),
            width=18,
            command=self._add_directory
        )
        btn_add_dir.pack(pady=5)

        btn_clear_list = HoverButton(
            controls_frame,
            bg_color=BG_HIGHLIGHT,
            fg_color=TEXT_WHITE,
            hover_bg="#4d4d4d",
            hover_fg=TEXT_WHITE,
            text="Limpiar Lista",
            font=("Arial", 10),
            width=18,
            command=self._clear_pending_list
        )
        btn_clear_list.pack(pady=(5, 30))

        # Botón para ejecutar el Análisis
        self.btn_analyze = HoverButton(
            controls_frame,
            bg_color=COLOR_SUCCESS,
            fg_color=TEXT_WHITE,
            hover_bg=COLOR_SUCCESS_HOVER,
            hover_fg=TEXT_WHITE,
            text="Analizar Imágenes",
            font=("Arial", 11, "bold"),
            width=18,
            command=self._start_analysis
        )
        self.btn_analyze.pack(side="bottom", pady=5)

        # Panel derecho para visualizar lista
        list_frame = tk.Frame(inner_frame, bg=BG_DARK)
        list_frame.pack(side="right", fill="both", expand=True)

        list_header_frame = tk.Frame(list_frame, bg=BG_DARK)
        list_header_frame.pack(fill="x")

        list_label = tk.Label(
            list_header_frame,
            text="IMÁGENES A ANALIZAR:",
            font=("Arial", 10, "bold"),
            bg=BG_DARK,
            fg=TEXT_WHITE
        )
        list_label.pack(side="left", pady=(0, 5))

        help_label = tk.Label(
            list_header_frame,
            text="(Doble clic en un archivo para quitarlo de la lista)",
            font=("Arial", 8, "italic"),
            bg=BG_DARK,
            fg=TEXT_MUTED
        )
        help_label.pack(side="right", pady=(0, 5))

        # Listbox y Scrollbar
        self.listbox = tk.Listbox(
            list_frame,
            bg=BG_PANEL,
            fg=TEXT_WHITE,
            selectbackground="#005999",
            selectforeground=TEXT_WHITE,
            bd=0,
            highlightthickness=0,
            font=("Consolas", 10)
        )
        self.listbox.pack(side="left", fill="both", expand=True)
        self.listbox.bind("<Double-Button-1>", self._remove_selected_from_listbox)

        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.listbox.yview, style="Vertical.TScrollbar")
        scrollbar.pack(side="right", fill="y")
        self.listbox.config(yscrollcommand=scrollbar.set)

    def _build_tab_results(self):
        # Frame interior
        inner_frame = tk.Frame(self.tab_results, bg=BG_DARK)
        inner_frame.pack(fill="both", expand=True, pady=10)

        # Botón de limpiar y exportar en la parte superior del tab
        top_bar = tk.Frame(inner_frame, bg=BG_DARK)
        top_bar.pack(fill="x", pady=(0, 10))

        btn_clear_results = HoverButton(
            top_bar,
            bg_color=COLOR_DANGER,
            fg_color=TEXT_WHITE,
            hover_bg=COLOR_DANGER_HOVER,
            hover_fg=TEXT_WHITE,
            text="Limpiar Resultados",
            font=("Arial", 9, "bold"),
            command=self._clear_treeview
        )
        btn_clear_results.pack(side="left", padx=5)

        btn_export = HoverButton(
            top_bar,
            bg_color=BG_HIGHLIGHT,
            fg_color=TEXT_WHITE,
            hover_bg="#4d4d4d",
            hover_fg=TEXT_WHITE,
            text="Exportar Resultados (JSON)...",
            font=("Arial", 9),
            command=self._export_results
        )
        btn_export.pack(side="left", padx=5)

        help_click_label = tk.Label(
            top_bar,
            text="(Doble clic en una fila para abrir la imagen)",
            font=("Arial", 8, "italic"),
            bg=BG_DARK,
            fg=TEXT_MUTED
        )
        help_click_label.pack(side="right", padx=5)

        # Contenedor de la Tabla
        table_container = tk.Frame(inner_frame, bg=BG_DARK)
        table_container.pack(fill="both", expand=True)

        # Definición de la Tabla (Treeview)
        columns = (
            "file", "resolution", "dpi", "sharpness", "brightness", "contrast", "aspect", "integrity"
        )
        self.tree = ttk.Treeview(
            table_container,
            columns=columns,
            show="headings",
            style="Treeview"
        )

        # Configurar encabezados y anchos de columnas
        column_configs = {
            "file": ("Archivo (Doble clic para abrir)", 220),
            "resolution": ("Resolución", 100),
            "dpi": ("Est. DPI", 90),
            "sharpness": ("Nitidez (Laplaciano)", 140),
            "brightness": ("Brillo", 110),
            "contrast": ("Contraste", 100),
            "aspect": ("Proporción", 110),
            "integrity": ("Integridad", 110),
        }

        for col, (title, width) in column_configs.items():
            self.tree.heading(col, text=title, anchor="w")
            self.tree.column(col, width=width, anchor="w")

        # Scrollbars para la tabla
        vsb = ttk.Scrollbar(table_container, orient="vertical", command=self.tree.yview, style="Vertical.TScrollbar")
        hsb = ttk.Scrollbar(table_container, orient="horizontal", command=self.tree.xview, style="Horizontal.TScrollbar")
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        # Grid layout para colocar scrollbars correctamente
        self.tree.grid(column=0, row=0, sticky="nsew")
        vsb.grid(column=1, row=0, sticky="ns")
        hsb.grid(column=0, row=1, sticky="ew")

        table_container.grid_columnconfigure(0, weight=1)
        table_container.grid_rowconfigure(0, weight=1)

        # Enlace doble clic para abrir la imagen
        self.tree.bind("<Double-1>", self._on_tree_double_click)

    # --- Acciones de Selección de Archivos ---

    def _add_files(self):
        filetypes = [
            ("Imágenes", "*.jpg *.jpeg *.png *.webp *.bmp *.tiff"),
            ("Todos los archivos", "*.*")
        ]
        paths = filedialog.askopenfilenames(
            title="Seleccionar imágenes",
            filetypes=filetypes
        )
        if not paths:
            return

        added_count = 0
        for p in paths:
            path = Path(p)
            if path.is_file() and path not in self.pending_files:
                self.pending_files.append(path)
                self.listbox.insert("end", str(path))
                added_count += 1

        self._update_status(f"Se añadieron {added_count} archivo(s) a la lista.")

    def _add_directory(self):
        dir_path = filedialog.askdirectory(title="Seleccionar carpeta de imágenes")
        if not dir_path:
            return

        directory = Path(dir_path)
        supported_exts = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}
        
        added_count = 0
        for entry in directory.iterdir():
            if entry.is_file() and entry.suffix.lower() in supported_exts:
                if entry not in self.pending_files:
                    self.pending_files.append(entry)
                    self.listbox.insert("end", str(entry))
                    added_count += 1

        self._update_status(f"Se añadieron {added_count} archivo(s) de la carpeta '{directory.name}'.")

    def _clear_pending_list(self):
        self.pending_files.clear()
        self.listbox.delete(0, "end")
        self._update_status("Lista de imágenes pendientes vaciada.")

    def _remove_selected_from_listbox(self, event):
        selection = self.listbox.curselection()
        if not selection:
            return
        idx = selection[0]
        val = self.listbox.get(idx)
        self.listbox.delete(idx)
        
        # Remover de la lista interna
        try:
            path_to_remove = Path(val)
            if path_to_remove in self.pending_files:
                self.pending_files.remove(path_to_remove)
                self._update_status(f"Removido: {path_to_remove.name}")
        except Exception:
            pass

    # --- Acciones de la Tabla de Resultados ---

    def _clear_treeview(self):
        items = self.tree.get_children()
        if not items:
            return
        if messagebox.askyesno("Confirmar Limpieza", "¿Seguro que deseas limpiar todos los resultados de la tabla?"):
            for item in items:
                self.tree.delete(item)
            self._update_status("Resultados de la tabla borrados.")

    def _on_tree_double_click(self, event):
        item_id = self.tree.focus()
        if not item_id:
            return
        values = self.tree.item(item_id, "values")
        if not values:
            return
        # El ID/filepath se guarda de forma oculta en la configuración o podemos buscar el archivo.
        # Vamos a almacenar la ruta absoluta de la imagen como el ID de la fila.
        filepath = item_id  # En nuestro Treeview, el item iid será la ruta del archivo.
        
        if os.path.exists(filepath):
            try:
                # Abrir archivo con visor del sistema
                if sys.platform.startswith("darwin"):
                    subprocess.call(("open", filepath))
                elif os.name == "nt":
                    os.startfile(filepath)
                else:
                    subprocess.call(("xdg-open", filepath))
            except Exception as e:
                messagebox.showerror("Error", f"No se pudo abrir el archivo:\n{e}")
        else:
            messagebox.showerror("Error", f"El archivo ya no existe en la ruta:\n{filepath}")

    def _export_results(self):
        items = self.tree.get_children()
        if not items:
            messagebox.showwarning("Sin resultados", "No hay resultados en la tabla para exportar.")
            return

        export_path = filedialog.asksaveasfilename(
            title="Exportar Resultados",
            defaultextension=".json",
            filetypes=[("Archivos JSON", "*.json")]
        )
        if not export_path:
            return

        import json
        results_data = []
        for item_id in items:
            vals = self.tree.item(item_id, "values")
            results_data.append({
                "ruta_absoluta": item_id,
                "archivo": vals[0],
                "resolucion": vals[1],
                "dpi_estimado": vals[2],
                "nitidez": vals[3],
                "brillo": vals[4],
                "contraste": vals[5],
                "proporcion": vals[6],
                "integridad": vals[7]
            })

        try:
            with open(export_path, "w", encoding="utf-8") as f:
                json.dump(results_data, f, indent=4, ensure_ascii=False)
            messagebox.showinfo("Exportación exitosa", f"Resultados exportados correctamente a:\n{export_path}")
        except Exception as e:
            messagebox.showerror("Error de exportación", f"No se pudo guardar el archivo:\n{e}")

    # --- Motor de Procesamiento en Hilo Secundario ---

    def _start_analysis(self):
        if self.is_processing:
            return

        if not self.pending_files:
            messagebox.showwarning("Lista Vacía", "Por favor añade al menos una imagen para analizar.")
            return

        self.is_processing = True
        self.btn_analyze.config(state="disabled", bg=BG_HIGHLIGHT)
        self.notebook.select(self.tab_results)  # Cambiar a la pestaña de resultados automáticamente

        total_files = len(self.pending_files)
        self.progress["maximum"] = total_files
        self.progress["value"] = 0
        self._update_status(f"Iniciando análisis de {total_files} imagen(es)...")

        # Iniciar hilo de trabajo
        worker = threading.Thread(target=self._analysis_worker, args=(list(self.pending_files),), daemon=True)
        worker.start()

        # Iniciar chequeo periódico de la cola en el hilo principal de la GUI
        self.root.after(100, self._check_queue)

    def _analysis_worker(self, files_list):
        for idx, file_path in enumerate(files_list):
            # Realizar el análisis intensivo
            metrics = analyze_single_image(file_path)
            
            # Enviar resultado y progreso a la cola
            self.queue.put({
                "type": "result",
                "data": metrics,
                "index": idx + 1,
                "total": len(files_list)
            })
            time.sleep(0.01)  # Pequeña pausa para asegurar fluidez

        # Indicar que terminó todo
        self.queue.put({"type": "done"})

    def _check_queue(self):
        try:
            while True:
                msg = self.queue.get_nowait()
                if msg["type"] == "result":
                    data = msg["data"]
                    filepath = data["filepath"]
                    
                    # Evitar duplicaciones en el Treeview eliminando el iid si ya existiera
                    if self.tree.exists(filepath):
                        self.tree.delete(filepath)

                    # Insertar en la tabla usando filepath como iid
                    self.tree.insert(
                        "",
                        "end",
                        iid=filepath,
                        values=(
                            data["filename"],
                            data["resolution"],
                            data["dpi_est"],
                            data["sharpness"],
                            data["brightness"],
                            data["contrast"],
                            data["aspect"],
                            data["integrity"]
                        )
                    )

                    # Aplicar tags visuales de color a las filas según si pasaron el control de calidad
                    if not data["is_ok"]:
                        self.tree.item(filepath, tags=("problematic",))
                    else:
                        self.tree.item(filepath, tags=("ok",))

                    # Configurar colores de los tags
                    self.tree.tag_configure("problematic", foreground="#ffb74d")  # Naranja claro para destacar advertencias
                    self.tree.tag_configure("ok", foreground=TEXT_WHITE)

                    # Actualizar barra de progreso y estado
                    self.progress["value"] = msg["index"]
                    self._update_status(f"Procesado ({msg['index']}/{msg['total']}): {data['filename']}")

                elif msg["type"] == "done":
                    self.is_processing = False
                    self.btn_analyze.config(state="normal", bg=COLOR_SUCCESS)
                    
                    # Vaciar la lista de imágenes pendientes analizadas
                    self._clear_pending_list()
                    
                    self._update_status("Análisis completado con éxito.")
                    messagebox.showinfo(
                        "Análisis Terminado",
                        "El análisis de calidad ha finalizado. Revisa los resultados en la tabla.\n\n"
                        "Puedes hacer doble clic sobre cualquier fila para visualizar la imagen original."
                    )
                    return
                
                self.queue.task_done()
        except queue.Empty:
            # Si no hay más mensajes por ahora, programar el siguiente chequeo
            if self.is_processing:
                self.root.after(100, self._check_queue)

    def _update_status(self, text):
        self.status_label.config(text=text)
        self.root.update_idletasks()


if __name__ == "__main__":
    root = tk.Tk()
    app = AnalyzerApp(root)
    root.mainloop()
