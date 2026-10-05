#!/usr/bin/env python3
"""
Analizador de Calidad de Imágenes
Un script autónomo con interfaz gráfica (Tkinter) para evaluar la calidad
de una o varias imágenes antes de procesarlas para PDF.
"""

import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

# Asegurar que el directorio raíz está en sys.path para importaciones de src
SCRIPT_DIR = Path(__file__).parent.absolute()
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

# Intentar importar librerías críticas para el análisis y dependencias del importador
try:
    import cv2  # noqa: F401
    import numpy as np  # noqa: F401
    from PIL import Image  # noqa: F401

    from src.deck_importer import DeckImportError, fetch_deck
    from src.quality import analyze_single_image
    from src.scryfall import ScryfallError, download_deck_images
except ImportError as e:
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror(
        "Faltan Dependencias",
        f"No se pudieron importar las dependencias necesarias. Por favor ejecuta:\n"
        f"uv pip install pillow opencv-python numpy requests\n\nDetalle: {e}",
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

# Colores del tema macOS Light
BG_DARK = "#eceef0"  # Fondo de la ventana principal (gris mac claro)
BG_PANEL = "#ffffff"  # Fondo de paneles y tarjetas (blanco)
BG_HIGHLIGHT = "#e5e5ea"  # Fondo de elementos destacados o bordes (gris suave)
TEXT_DARK = "#1d1d1f"  # Texto principal (negro mac, casi negro)
TEXT_MUTED = "#86868b"  # Texto secundario/desactivado (gris mac)
TEXT_WHITE = "#ffffff"  # Texto blanco puro para botones primarios
COLOR_PRIMARY = "#007aff"  # Accent color de macOS (azul sistema)
COLOR_PRIMARY_HOVER = "#0062cc"  # Accent color hover
COLOR_SUCCESS = "#34c759"  # Éxito (verde macOS)
COLOR_SUCCESS_HOVER = "#28a745"
COLOR_WARNING = "#ff9500"  # Advertencia (naranja macOS)
COLOR_DANGER = "#ff3b30"  # Peligro/Error (rojo macOS)
COLOR_DANGER_HOVER = "#e02d24"

# Fuentes del sistema para un aspecto nativo de macOS / Windows / Linux
FONT_FAMILY = (
    ".AppleSystemUIFont"
    if sys.platform.startswith("darwin")
    else "Segoe UI"
    if os.name == "nt"
    else "Arial"
)
FONT_MONO = (
    "Menlo" if sys.platform.startswith("darwin") else "Consolas" if os.name == "nt" else "monospace"
)


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
            **kw,
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


class AnalyzerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Analizador de Calidad de Imágenes — MPCFillToPDF Utility")
        self.root.geometry("1000x650")
        self.root.configure(bg=BG_DARK)

        # Habilitar soporte de alta densidad (DPI)
        try:
            self.root.tk.call("tk", "scaling", 1.5)
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
        style.configure("TNotebook", background=BG_DARK, borderwidth=0, highlightthickness=0)
        style.configure(
            "TNotebook.Tab",
            background=BG_HIGHLIGHT,
            foreground=TEXT_MUTED,
            font=(FONT_FAMILY, 10, "bold"),
            padding=[15, 6],
            relief="flat",
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", BG_PANEL)],
            foreground=[("selected", TEXT_DARK)],
        )

        # Configurar estilo de Treeview
        style.configure(
            "Treeview",
            background=BG_PANEL,
            foreground=TEXT_DARK,
            fieldbackground=BG_PANEL,
            rowheight=28,
            font=(FONT_FAMILY, 9),
            borderwidth=0,
        )
        style.configure(
            "Treeview.Heading",
            background=BG_DARK,
            foreground=TEXT_MUTED,
            font=(FONT_FAMILY, 9, "bold"),
            relief="flat",
        )
        style.map(
            "Treeview",
            background=[("selected", COLOR_PRIMARY)],
            foreground=[("selected", TEXT_WHITE)],
        )
        style.map("Treeview.Heading", background=[("active", BG_HIGHLIGHT)])

        # Barra de progreso
        style.configure(
            "TProgressbar", troughcolor=BG_HIGHLIGHT, background=COLOR_PRIMARY, thickness=14
        )

        # Scrollbars
        style.configure(
            "Vertical.TScrollbar",
            gripcount=0,
            background=BG_HIGHLIGHT,
            troughcolor=BG_DARK,
            bordercolor=BG_DARK,
            arrowcolor=TEXT_DARK,
        )
        style.configure(
            "Horizontal.TScrollbar",
            gripcount=0,
            background=BG_HIGHLIGHT,
            troughcolor=BG_DARK,
            bordercolor=BG_DARK,
            arrowcolor=TEXT_DARK,
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
            font=(FONT_FAMILY, 16, "bold"),
            bg=BG_DARK,
            fg=TEXT_DARK,
        )
        title_label.pack(side="left")

        subtitle_label = tk.Label(
            header_frame,
            text="Métricas: Resolución, DPI, Nitidez, Brillo, Contraste y Aspecto",
            font=(FONT_FAMILY, 9, "italic"),
            bg=BG_DARK,
            fg=TEXT_MUTED,
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
            font=(FONT_FAMILY, 9),
            bg=BG_DARK,
            fg=TEXT_MUTED,
        )
        self.status_label.pack(side="left")

        self.progress = ttk.Progressbar(
            self.status_frame, orient="horizontal", mode="determinate", style="TProgressbar"
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
            font=(FONT_FAMILY, 10, "bold"),
            bg=BG_PANEL,
            fg=TEXT_DARK,
        )
        actions_label.pack(anchor="w", pady=(0, 15))

        # Botones de control
        self.btn_add_files = HoverButton(
            controls_frame,
            bg_color=COLOR_PRIMARY,
            fg_color=TEXT_WHITE,
            hover_bg=COLOR_PRIMARY_HOVER,
            hover_fg=TEXT_WHITE,
            text="Añadir Imágenes...",
            font=(FONT_FAMILY, 10, "bold"),
            width=18,
            command=self._add_files,
        )
        self.btn_add_files.pack(pady=5)

        self.btn_add_dir = HoverButton(
            controls_frame,
            bg_color=COLOR_PRIMARY,
            fg_color=TEXT_WHITE,
            hover_bg=COLOR_PRIMARY_HOVER,
            hover_fg=TEXT_WHITE,
            text="Añadir Carpeta...",
            font=(FONT_FAMILY, 10, "bold"),
            width=18,
            command=self._add_directory,
        )
        self.btn_add_dir.pack(pady=5)

        self.btn_add_moxfield = HoverButton(
            controls_frame,
            bg_color=COLOR_PRIMARY,
            fg_color=TEXT_WHITE,
            hover_bg=COLOR_PRIMARY_HOVER,
            hover_fg=TEXT_WHITE,
            text="Añadir desde Moxfield...",
            font=(FONT_FAMILY, 10, "bold"),
            width=18,
            command=self._add_moxfield_deck,
        )
        self.btn_add_moxfield.pack(pady=5)

        self.btn_clear_list = HoverButton(
            controls_frame,
            bg_color=BG_HIGHLIGHT,
            fg_color=TEXT_DARK,
            hover_bg="#d2d2d7",
            hover_fg=TEXT_DARK,
            text="Limpiar Lista",
            font=(FONT_FAMILY, 10),
            width=18,
            command=self._clear_pending_list,
        )
        self.btn_clear_list.pack(pady=(5, 30))

        # Botón para ejecutar el Análisis
        self.btn_analyze = HoverButton(
            controls_frame,
            bg_color=COLOR_SUCCESS,
            fg_color=TEXT_WHITE,
            hover_bg=COLOR_SUCCESS_HOVER,
            hover_fg=TEXT_WHITE,
            text="Analizar Imágenes",
            font=(FONT_FAMILY, 11, "bold"),
            width=18,
            command=self._start_analysis,
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
            font=(FONT_FAMILY, 10, "bold"),
            bg=BG_DARK,
            fg=TEXT_DARK,
        )
        list_label.pack(side="left", pady=(0, 5))

        help_label = tk.Label(
            list_header_frame,
            text="(Doble clic en un archivo para quitarlo de la lista)",
            font=(FONT_FAMILY, 8, "italic"),
            bg=BG_DARK,
            fg=TEXT_MUTED,
        )
        help_label.pack(side="right", pady=(0, 5))

        # Listbox y Scrollbar
        self.listbox = tk.Listbox(
            list_frame,
            bg=BG_PANEL,
            fg=TEXT_DARK,
            selectbackground=COLOR_PRIMARY,
            selectforeground=TEXT_WHITE,
            bd=1,
            highlightthickness=1,
            highlightbackground=BG_HIGHLIGHT,
            highlightcolor=COLOR_PRIMARY,
            font=(FONT_MONO, 10),
        )
        self.listbox.pack(side="left", fill="both", expand=True)
        self.listbox.bind("<Double-Button-1>", self._remove_selected_from_listbox)

        scrollbar = ttk.Scrollbar(
            list_frame, orient="vertical", command=self.listbox.yview, style="Vertical.TScrollbar"
        )
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
            font=(FONT_FAMILY, 9, "bold"),
            command=self._clear_treeview,
        )
        btn_clear_results.pack(side="left", padx=5)

        btn_export = HoverButton(
            top_bar,
            bg_color=BG_HIGHLIGHT,
            fg_color=TEXT_DARK,
            hover_bg="#d2d2d7",
            hover_fg=TEXT_DARK,
            text="Exportar Resultados (JSON)...",
            font=(FONT_FAMILY, 9),
            command=self._export_results,
        )
        btn_export.pack(side="left", padx=5)

        help_click_label = tk.Label(
            top_bar,
            text="(Doble clic en una fila para abrir la imagen)",
            font=(FONT_FAMILY, 8, "italic"),
            bg=BG_DARK,
            fg=TEXT_MUTED,
        )
        help_click_label.pack(side="right", padx=5)

        # Contenedor de la Tabla
        table_container = tk.Frame(inner_frame, bg=BG_DARK)
        table_container.pack(fill="both", expand=True)

        # Definición de la Tabla (Treeview)
        columns = (
            "file",
            "resolution",
            "dpi",
            "sharpness",
            "brightness",
            "contrast",
            "aspect",
            "integrity",
        )
        self.tree = ttk.Treeview(
            table_container, columns=columns, show="headings", style="Treeview"
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
        vsb = ttk.Scrollbar(
            table_container, orient="vertical", command=self.tree.yview, style="Vertical.TScrollbar"
        )
        hsb = ttk.Scrollbar(
            table_container,
            orient="horizontal",
            command=self.tree.xview,
            style="Horizontal.TScrollbar",
        )
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
            ("Todos los archivos", "*.*"),
        ]
        paths = filedialog.askopenfilenames(title="Seleccionar imágenes", filetypes=filetypes)
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

        self._update_status(
            f"Se añadieron {added_count} archivo(s) de la carpeta '{directory.name}'."
        )

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
        if messagebox.askyesno(
            "Confirmar Limpieza", "¿Seguro que deseas limpiar todos los resultados de la tabla?"
        ):
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
            filetypes=[("Archivos JSON", "*.json")],
        )
        if not export_path:
            return

        import json

        results_data = []
        for item_id in items:
            vals = self.tree.item(item_id, "values")
            results_data.append(
                {
                    "ruta_absoluta": item_id,
                    "archivo": vals[0],
                    "resolucion": vals[1],
                    "dpi_estimado": vals[2],
                    "nitidez": vals[3],
                    "brillo": vals[4],
                    "contraste": vals[5],
                    "proporcion": vals[6],
                    "integridad": vals[7],
                }
            )

        try:
            with open(export_path, "w", encoding="utf-8") as f:
                json.dump(results_data, f, indent=4, ensure_ascii=False)
            messagebox.showinfo(
                "Exportación exitosa", f"Resultados exportados correctamente a:\n{export_path}"
            )
        except Exception as e:
            messagebox.showerror("Error de exportación", f"No se pudo guardar el archivo:\n{e}")

    # --- Motor de Procesamiento en Hilo Secundario ---

    def _start_analysis(self):
        if self.is_processing:
            return

        if not self.pending_files:
            messagebox.showwarning(
                "Lista Vacía", "Por favor añade al menos una imagen para analizar."
            )
            return

        self.is_processing = True
        self._set_controls_state("disabled")
        self.notebook.select(self.tab_results)  # Cambiar a la pestaña de resultados automáticamente

        total_files = len(self.pending_files)
        self.progress["maximum"] = total_files
        self.progress["value"] = 0
        self._update_status(f"Iniciando análisis de {total_files} imagen(es)...")

        # Iniciar hilo de trabajo
        worker = threading.Thread(
            target=self._analysis_worker, args=(list(self.pending_files),), daemon=True
        )
        worker.start()

        # Iniciar chequeo periódico de la cola en el hilo principal de la GUI
        self.root.after(100, self._check_queue)

    def _analysis_worker(self, files_list):
        for idx, file_path in enumerate(files_list):
            # Realizar el análisis intensivo
            metrics = analyze_single_image(file_path)

            # Enviar resultado y progreso a la cola
            self.queue.put(
                {"type": "result", "data": metrics, "index": idx + 1, "total": len(files_list)}
            )
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
                            data["integrity"],
                        ),
                    )

                    # Aplicar tags visuales de color a las filas según si pasaron el control de calidad
                    if not data["is_ok"]:
                        self.tree.item(filepath, tags=("problematic",))
                    else:
                        self.tree.item(filepath, tags=("ok",))

                    # Configurar colores de los tags
                    self.tree.tag_configure(
                        "problematic", foreground=COLOR_DANGER
                    )  # Rojo macOS para destacar advertencias
                    self.tree.tag_configure("ok", foreground=TEXT_DARK)

                    # Actualizar barra de progreso y estado
                    self.progress["value"] = msg["index"]
                    self._update_status(
                        f"Procesado ({msg['index']}/{msg['total']}): {data['filename']}"
                    )

                elif msg["type"] == "done":
                    self.is_processing = False
                    self._set_controls_state("normal")

                    # Vaciar la lista de imágenes pendientes analizadas
                    self._clear_pending_list()

                    self._update_status("Análisis completado con éxito.")
                    messagebox.showinfo(
                        "Análisis Terminado",
                        "El análisis de calidad ha finalizado. Revisa los resultados en la tabla.\n\n"
                        "Puedes hacer doble clic sobre cualquier fila para visualizar la imagen original.",
                    )
                    return

                elif msg["type"] == "download_progress":
                    self.progress["maximum"] = msg["total"]
                    self.progress["value"] = msg["done"]
                    self._update_status(msg["text"])

                elif msg["type"] == "download_done":
                    self.is_processing = False
                    self._set_controls_state("normal")
                    self.progress["value"] = 0

                    # Añadir las imágenes descargadas a la lista
                    added_count = 0
                    for p in msg["files"]:
                        if p not in self.pending_files:
                            self.pending_files.append(p)
                            self.listbox.insert("end", str(p))
                            added_count += 1

                    self._update_status(
                        f"Importación completada. Se añadieron {added_count} imágenes del mazo '{msg['deck_name']}'."
                    )
                    messagebox.showinfo(
                        "Importación Exitosa",
                        f"Se importó el mazo '{msg['deck_name']}' con éxito.\n"
                        f"Se añadieron {added_count} imágenes nuevas a la lista para analizar.",
                    )
                    return

                elif msg["type"] == "download_error":
                    self.is_processing = False
                    self._set_controls_state("normal")
                    self.progress["value"] = 0
                    self._update_status("Error durante la importación.")
                    messagebox.showerror("Error de Importación", msg["message"])
                    return

                self.queue.task_done()
        except queue.Empty:
            # Si no hay más mensajes por ahora, programar el siguiente chequeo
            if self.is_processing:
                self.root.after(100, self._check_queue)

    def _set_controls_state(self, state):
        for btn in (
            self.btn_add_files,
            self.btn_add_dir,
            self.btn_add_moxfield,
            self.btn_clear_list,
            self.btn_analyze,
        ):
            if btn:
                if state == "disabled":
                    btn.config(state="disabled", bg=BG_HIGHLIGHT, fg=TEXT_MUTED)
                else:
                    if btn == self.btn_analyze:
                        btn.config(state="normal", bg=COLOR_SUCCESS, fg=TEXT_WHITE)
                    elif btn == self.btn_clear_list:
                        btn.config(state="normal", bg=BG_HIGHLIGHT, fg=TEXT_DARK)
                    else:
                        btn.config(state="normal", bg=COLOR_PRIMARY, fg=TEXT_WHITE)

    def _add_moxfield_deck(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("Importar Mazo Moxfield")
        dlg.geometry("500x180")
        dlg.configure(bg=BG_DARK)
        dlg.resizable(False, False)
        dlg.transient(self.root)
        dlg.grab_set()

        # Centrar diálogo
        dlg.update_idletasks()
        w = dlg.winfo_width()
        h = dlg.winfo_height()
        extra_x = (self.root.winfo_width() - w) // 2
        extra_y = (self.root.winfo_height() - h) // 2
        dlg.geometry(f"+{self.root.winfo_x() + extra_x}+{self.root.winfo_y() + extra_y}")

        lbl_title = tk.Label(
            dlg,
            text="Importar mazo desde URL (Moxfield, Archidekt, etc.)",
            font=(FONT_FAMILY, 11, "bold"),
            bg=BG_DARK,
            fg=TEXT_DARK,
        )
        lbl_title.pack(anchor="w", padx=15, pady=(15, 5))

        lbl_desc = tk.Label(
            dlg,
            text="Introduce la URL del mazo a analizar:",
            font=(FONT_FAMILY, 9),
            bg=BG_DARK,
            fg=TEXT_MUTED,
        )
        lbl_desc.pack(anchor="w", padx=15, pady=(0, 10))

        url_frame = tk.Frame(dlg, bg=BG_DARK)
        url_frame.pack(fill="x", padx=15, pady=5)

        url_var = tk.StringVar()
        url_entry = tk.Entry(
            url_frame,
            textvariable=url_var,
            bg=BG_PANEL,
            fg=TEXT_DARK,
            insertbackground=TEXT_DARK,
            relief="flat",
            font=(FONT_MONO, 10),
            highlightthickness=1,
            highlightbackground=BG_HIGHLIGHT,
            highlightcolor=COLOR_PRIMARY,
        )
        url_entry.pack(fill="x", ipady=4)
        url_entry.focus_set()

        btn_frame = tk.Frame(dlg, bg=BG_DARK)
        btn_frame.pack(fill="x", padx=15, pady=(15, 10))

        def on_import():
            url = url_var.get().strip()
            if not url:
                messagebox.showwarning("Campo Vacío", "Por favor ingresa una URL.", parent=dlg)
                return
            dlg.destroy()
            self._start_moxfield_import(url)

        def on_cancel():
            dlg.destroy()

        btn_import = HoverButton(
            btn_frame,
            bg_color=COLOR_PRIMARY,
            fg_color=TEXT_WHITE,
            hover_bg=COLOR_PRIMARY_HOVER,
            hover_fg=TEXT_WHITE,
            text="Importar",
            font=(FONT_FAMILY, 10, "bold"),
            command=on_import,
        )
        btn_import.pack(side="right", padx=(10, 0))

        btn_cancel = HoverButton(
            btn_frame,
            bg_color=BG_HIGHLIGHT,
            fg_color=TEXT_DARK,
            hover_bg="#d2d2d7",
            hover_fg=TEXT_DARK,
            text="Cancelar",
            font=(FONT_FAMILY, 10),
            command=on_cancel,
        )
        btn_cancel.pack(side="right")

        url_entry.bind("<Return>", lambda e: on_import())
        url_entry.bind("<Escape>", lambda e: on_cancel())

    def _start_moxfield_import(self, url):
        if self.is_processing:
            return

        self.is_processing = True
        self._set_controls_state("disabled")

        self.progress["maximum"] = 100
        self.progress["value"] = 0
        self._update_status("Iniciando importación desde la URL del mazo...")

        worker = threading.Thread(target=self._moxfield_import_worker, args=(url,), daemon=True)
        worker.start()

        self.root.after(100, self._check_queue)

    def _moxfield_import_worker(self, url):
        try:
            self.queue.put(
                {
                    "type": "download_progress",
                    "done": 0,
                    "total": 1,
                    "text": "Obteniendo datos del mazo...",
                }
            )
            deck = fetch_deck(url)

            cards = deck.cards
            if not cards:
                self.queue.put(
                    {"type": "download_error", "message": "El mazo importado no contiene cartas."}
                )
                return

            dest_dir = Path(__file__).parent.absolute() / "workdir" / "scryfall_analyzer"
            dest_dir.mkdir(parents=True, exist_ok=True)

            def progress_cb(done, total):
                self.queue.put(
                    {
                        "type": "download_progress",
                        "done": done,
                        "total": total,
                        "text": f"Descargando imágenes de Scryfall ({done}/{total})....",
                    }
                )

            dl_results = download_deck_images(cards, dest_dir, progress_cb=progress_cb)

            downloaded_paths = []
            for card, front_path, back_path in dl_results:
                if front_path and front_path.exists():
                    downloaded_paths.append(front_path)
                if back_path and back_path.exists():
                    downloaded_paths.append(back_path)

            self.queue.put(
                {"type": "download_done", "files": downloaded_paths, "deck_name": deck.name}
            )

        except DeckImportError as e:
            self.queue.put({"type": "download_error", "message": f"Error al importar el mazo: {e}"})
        except ScryfallError as e:
            self.queue.put(
                {"type": "download_error", "message": f"Error al descargar de Scryfall: {e}"}
            )
        except Exception as e:
            self.queue.put({"type": "download_error", "message": f"Error inesperado: {e}"})

    def _update_status(self, text):
        self.status_label.config(text=text)
        self.root.update_idletasks()


if __name__ == "__main__":
    root = tk.Tk()
    app = AnalyzerApp(root)
    root.mainloop()
