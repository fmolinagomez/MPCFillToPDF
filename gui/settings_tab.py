"""Settings tab mixin — ⚙ Configuración."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import colorchooser, filedialog, ttk

from gui.paths import app_base_dir, output_dir
from src.app_settings import (
    DEFAULT_CUT_LINE_COLOR,
    DEFAULT_CUT_LINE_OVER_CARDS,
    DEFAULT_CUT_LINE_STYLE,
    DEFAULT_CUT_LINE_WIDTH,
    save_settings,
)

_WIDTH_MIN = 0.1
_WIDTH_MAX = 10.0


class SettingsTabMixin:
    """Mixin that builds the Configuración tab and exposes _settings: AppSettings."""

    def _build_settings_tab(self, frame: ttk.Frame) -> None:
        outer = ttk.Frame(frame)
        outer.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)

        # ── Ruta de exportación ─────────────────────────────────────────────
        ttk.Label(outer, text="Carpeta de exportación de PDFs", font=("Segoe UI", 10, "bold")).pack(
            anchor=tk.W, pady=(0, 2)
        )

        self._settings_out_var = tk.StringVar(value=str(self._settings.output_dir or output_dir()))
        dir_row = ttk.Frame(outer)
        dir_row.pack(fill=tk.X, pady=(0, 12))
        ttk.Label(
            dir_row, textvariable=self._settings_out_var, foreground="#444", anchor=tk.W
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(dir_row, text="Examinar…", command=self._settings_pick_dir, width=10).pack(
            side=tk.RIGHT, padx=(8, 0)
        )

        ttk.Separator(outer, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(0, 12))

        # ── Líneas de corte ──────────────────────────────────────────────────
        ttk.Label(outer, text="Líneas de corte", font=("Segoe UI", 10, "bold")).pack(
            anchor=tk.W, pady=(0, 6)
        )

        # Color
        color_row = ttk.Frame(outer)
        color_row.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(color_row, text="Color:").pack(side=tk.LEFT)
        self._color_swatch = tk.Label(
            color_row,
            width=4,
            relief=tk.SOLID,
            borderwidth=1,
            bg=self._settings.cut_line_color,
        )
        self._color_swatch.pack(side=tk.LEFT, padx=(6, 4))
        self._settings_color_var = tk.StringVar(value=self._settings.cut_line_color)
        ttk.Label(color_row, textvariable=self._settings_color_var, foreground="#555").pack(
            side=tk.LEFT, padx=(0, 8)
        )
        ttk.Button(color_row, text="Elegir color…", command=self._settings_pick_color).pack(
            side=tk.LEFT
        )

        # Grosor
        width_row = ttk.Frame(outer)
        width_row.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(width_row, text="Grosor (pt):").pack(side=tk.LEFT)
        self._settings_width_var = tk.StringVar(value=f"{self._settings.cut_line_width:.1f}")
        width_spin = ttk.Spinbox(
            width_row,
            from_=_WIDTH_MIN,
            to=_WIDTH_MAX,
            increment=0.1,
            format="%.1f",
            textvariable=self._settings_width_var,
            width=6,
            command=self._on_settings_width_changed,
        )
        width_spin.pack(side=tk.LEFT, padx=(6, 0))
        self._settings_width_var.trace_add("write", self._on_settings_width_trace)

        # Estilo
        ttk.Label(outer, text="Estilo:").pack(anchor=tk.W, pady=(6, 2))
        self._settings_style_var = tk.StringVar(value=self._settings.cut_line_style)
        style_frame = ttk.Frame(outer)
        style_frame.pack(anchor=tk.W, pady=(0, 2))
        ttk.Radiobutton(
            style_frame,
            text="Marcas de margen  (para copistería)",
            variable=self._settings_style_var,
            value="ticks",
            command=self._on_settings_style_changed,
        ).pack(anchor=tk.W)
        ttk.Radiobutton(
            style_frame,
            text="Líneas completas de lado a lado  (para autocorte)",
            variable=self._settings_style_var,
            value="full",
            command=self._on_settings_style_changed,
        ).pack(anchor=tk.W)

        # Dibujar sobre las cartas
        self._settings_over_cards_var = tk.BooleanVar(value=self._settings.cut_line_over_cards)
        ttk.Checkbutton(
            outer,
            text="Dibujar sobre las cartas  (Para autocorte)",
            variable=self._settings_over_cards_var,
            command=self._on_settings_over_cards_changed,
        ).pack(anchor=tk.W, pady=(6, 0))
        ttk.Label(
            outer,
            text="Las líneas se dibujan encima de las imágenes para que sean visibles al cortar.",
            foreground="#888",
            font=("Segoe UI", 8),
        ).pack(anchor=tk.W, pady=(1, 12))

        ttk.Separator(outer, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(0, 12))

        # ── Botón de restablecer ─────────────────────────────────────────────
        ttk.Button(
            outer,
            text="Volver a valores predeterminados",
            command=self._settings_reset,
        ).pack(anchor=tk.W)

    def _settings_pick_dir(self) -> None:
        current = self._settings.output_dir or output_dir()
        chosen = filedialog.askdirectory(
            title="Selecciona la carpeta de exportación de PDFs",
            initialdir=str(current),
        )
        if chosen:
            p = Path(chosen)
            self._settings.output_dir = p
            self._custom_output_dir = p
            self._settings_out_var.set(str(p))
            self.out_dir_var.set(str(p))
            self._persist_settings()

    def _settings_pick_color(self) -> None:
        result = colorchooser.askcolor(
            color=self._settings.cut_line_color,
            title="Elige el color de las líneas de corte",
        )
        if result and result[1]:
            hex_color: str = result[1]
            self._settings.cut_line_color = hex_color
            self._settings_color_var.set(hex_color)
            self._color_swatch.configure(bg=hex_color)
            self._persist_settings()

    def _on_settings_width_changed(self) -> None:
        """Called by Spinbox arrow buttons."""
        self._apply_width_from_var()

    def _on_settings_width_trace(self, *_) -> None:
        """Called on every keystroke in the Spinbox entry."""
        self._apply_width_from_var()

    def _apply_width_from_var(self) -> None:
        try:
            val = float(self._settings_width_var.get().replace(",", "."))
            val = max(_WIDTH_MIN, min(_WIDTH_MAX, val))
            self._settings.cut_line_width = val
            self._persist_settings()
        except ValueError:
            pass

    def _on_settings_style_changed(self) -> None:
        self._settings.cut_line_style = self._settings_style_var.get()
        self._persist_settings()

    def _on_settings_over_cards_changed(self) -> None:
        self._settings.cut_line_over_cards = self._settings_over_cards_var.get()
        self._persist_settings()

    def _settings_reset(self) -> None:
        self._settings.output_dir = None
        self._settings.cut_line_color = DEFAULT_CUT_LINE_COLOR
        self._settings.cut_line_style = DEFAULT_CUT_LINE_STYLE
        self._settings.cut_line_width = DEFAULT_CUT_LINE_WIDTH
        self._settings.cut_line_over_cards = DEFAULT_CUT_LINE_OVER_CARDS

        self._custom_output_dir = None
        self.out_dir_var.set(str(output_dir()))
        self._settings_out_var.set(str(output_dir()))
        self._settings_color_var.set(DEFAULT_CUT_LINE_COLOR)
        self._color_swatch.configure(bg=DEFAULT_CUT_LINE_COLOR)
        self._settings_style_var.set(DEFAULT_CUT_LINE_STYLE)
        self._settings_width_var.set(f"{DEFAULT_CUT_LINE_WIDTH:.1f}")
        self._settings_over_cards_var.set(DEFAULT_CUT_LINE_OVER_CARDS)
        self._persist_settings()

    def _persist_settings(self) -> None:
        save_settings(self._settings, app_base_dir())
