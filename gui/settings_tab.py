"""Settings tab mixin — ⚙ Configuración."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import colorchooser, filedialog, ttk

from gui.paths import app_base_dir, output_dir
from src.app_settings import (
    DEFAULT_CUT_LINE_COLOR,
    DEFAULT_CUT_LINE_OVER_BACKS,
    DEFAULT_CUT_LINE_OVER_CARDS,
    DEFAULT_CUT_LINE_OVER_FRONTS,
    DEFAULT_CUT_LINE_STYLE,
    DEFAULT_CUT_LINE_WIDTH,
    DEFAULT_SCRYFALL_LANG,
    DEFAULT_SCRYFALL_QUALITY,
    DEFAULT_SCRYFALL_FAIL_POLICY,
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

        # Contenedor de dos columnas
        cols_frame = ttk.Frame(outer)
        cols_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 12))

        left_col = ttk.Frame(cols_frame)
        left_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 12))

        right_col = ttk.Frame(cols_frame)
        right_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(12, 0))

        # ── Líneas de corte (Columna Izquierda) ──────────────────────────────
        ttk.Label(left_col, text="Líneas de corte", font=("Segoe UI", 10, "bold")).pack(
            anchor=tk.W, pady=(0, 6)
        )

        # Color
        color_row = ttk.Frame(left_col)
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
        width_row = ttk.Frame(left_col)
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
        ttk.Label(left_col, text="Estilo:").pack(anchor=tk.W, pady=(6, 2))
        self._settings_style_var = tk.StringVar(value=self._settings.cut_line_style)
        self._style_frame = ttk.Frame(left_col)
        style_frame = self._style_frame
        style_frame.pack(anchor=tk.W, pady=(0, 0))
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

        self._over_cards_sub_frame = ttk.Frame(left_col)
        self._settings_over_fronts_var = tk.BooleanVar(value=self._settings.cut_line_over_fronts)
        self._settings_over_backs_var = tk.BooleanVar(value=self._settings.cut_line_over_backs)
        ttk.Checkbutton(
            self._over_cards_sub_frame,
            text="Frontal",
            variable=self._settings_over_fronts_var,
            command=self._on_settings_over_fronts_changed,
        ).pack(anchor=tk.W)
        ttk.Checkbutton(
            self._over_cards_sub_frame,
            text="Trasero",
            variable=self._settings_over_backs_var,
            command=self._on_settings_over_backs_changed,
        ).pack(anchor=tk.W)
        if self._settings.cut_line_style == "full":
            self._over_cards_sub_frame.pack(anchor=tk.W, padx=(20, 0), pady=(4, 0))

        # ── Configuración avanzada de Scryfall (Columna Derecha) ─────────────
        sf_frame = ttk.LabelFrame(right_col, text=" Configuración avanzada de Scryfall ", padding=10)
        sf_frame.pack(fill=tk.BOTH, expand=True)

        # Idioma preferido
        lang_row = ttk.Frame(sf_frame)
        lang_row.pack(fill=tk.X, pady=4)
        ttk.Label(lang_row, text="Idioma preferido:").pack(side=tk.LEFT)

        self._scryfall_lang_options = ["Español", "Inglés"]
        self._scryfall_lang_map = {"Español": "es", "Inglés": "en"}
        self._scryfall_lang_inv_map = {"es": "Español", "en": "Inglés"}

        initial_lang_name = self._scryfall_lang_inv_map.get(self._settings.scryfall_lang, "Inglés")
        self._settings_sf_lang_var = tk.StringVar(value=initial_lang_name)

        self._sf_lang_combo = ttk.Combobox(
            lang_row,
            values=self._scryfall_lang_options,
            textvariable=self._settings_sf_lang_var,
            state="readonly",
            width=15,
        )
        self._sf_lang_combo.pack(side=tk.LEFT, padx=(8, 0))
        self._sf_lang_combo.bind("<<ComboboxSelected>>", self._on_sf_lang_changed)

        # Calidad de imagen
        quality_row = ttk.Frame(sf_frame)
        quality_row.pack(fill=tk.X, pady=4)
        ttk.Label(quality_row, text="Calidad de imagen:").pack(side=tk.LEFT)

        self._scryfall_quality_options = ["Large (Equilibrada)", "PNG (Alta calidad)"]
        self._scryfall_quality_map = {"Large (Equilibrada)": "large", "PNG (Alta calidad)": "png"}
        self._scryfall_quality_inv_map = {"large": "Large (Equilibrada)", "png": "PNG (Alta calidad)"}

        initial_quality_name = self._scryfall_quality_inv_map.get(self._settings.scryfall_quality, "Large (Equilibrada)")
        self._settings_sf_quality_var = tk.StringVar(value=initial_quality_name)

        self._sf_quality_combo = ttk.Combobox(
            quality_row,
            values=self._scryfall_quality_options,
            textvariable=self._settings_sf_quality_var,
            state="readonly",
            width=20,
        )
        self._sf_quality_combo.pack(side=tk.LEFT, padx=(8, 0))
        self._sf_quality_combo.bind("<<ComboboxSelected>>", self._on_sf_quality_changed)

        # Política de error
        policy_row = ttk.Frame(sf_frame)
        policy_row.pack(fill=tk.X, pady=4)
        self._sf_policy_label = ttk.Label(policy_row, text="En caso de fallo en idioma preferido:")
        self._sf_policy_label.pack(anchor=tk.W)

        self._scryfall_policy_options = [
            "Descargar en inglés",
            "Buscar versión alternativa en idioma preferido y si no descargar en inglés"
        ]
        self._scryfall_policy_map = {
            "Descargar en inglés": "english",
            "Buscar versión alternativa en idioma preferido y si no descargar en inglés": "alternative"
        }
        self._scryfall_policy_inv_map = {
            "english": "Descargar en inglés",
            "alternative": "Buscar versión alternativa en idioma preferido y si no descargar en inglés"
        }

        initial_policy_name = self._scryfall_policy_inv_map.get(self._settings.scryfall_fail_policy, "Descargar en inglés")
        self._settings_sf_policy_var = tk.StringVar(value=initial_policy_name)

        self._sf_policy_combo = ttk.Combobox(
            policy_row,
            values=self._scryfall_policy_options,
            textvariable=self._settings_sf_policy_var,
            state="readonly",
        )
        self._sf_policy_combo.pack(fill=tk.X, pady=(2, 0))
        self._sf_policy_combo.bind("<<ComboboxSelected>>", self._on_sf_policy_changed)

        # Configurar estado inicial según idioma
        self._update_sf_policy_state()

        ttk.Separator(outer, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(8, 12))

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
        is_full = self._settings.cut_line_style == "full"
        self._settings.cut_line_over_cards = is_full
        if is_full:
            self._settings_over_fronts_var.set(True)
            self._settings_over_backs_var.set(True)
            self._settings.cut_line_over_fronts = True
            self._settings.cut_line_over_backs = True
            self._over_cards_sub_frame.pack(
                anchor=tk.W, padx=(20, 0), pady=(4, 0), after=self._style_frame
            )
        else:
            self._over_cards_sub_frame.pack_forget()
        self._persist_settings()

    def _on_settings_over_fronts_changed(self) -> None:
        self._settings.cut_line_over_fronts = self._settings_over_fronts_var.get()
        self._persist_settings()

    def _on_settings_over_backs_changed(self) -> None:
        self._settings.cut_line_over_backs = self._settings_over_backs_var.get()
        self._persist_settings()

    def _update_sf_policy_state(self) -> None:
        """Enable or disable the policy selection depending on the preferred language."""
        lang_val = self._scryfall_lang_map.get(self._settings_sf_lang_var.get(), "en")
        if lang_val == "en":
            self._sf_policy_combo.configure(state="disabled")
            self._sf_policy_label.configure(foreground="#888")
        else:
            self._sf_policy_combo.configure(state="readonly")
            self._sf_policy_label.configure(foreground="")

    def _on_sf_lang_changed(self, _event=None) -> None:
        lang_name = self._settings_sf_lang_var.get()
        lang_code = self._scryfall_lang_map.get(lang_name, "en")
        self._settings.scryfall_lang = lang_code
        self._update_sf_policy_state()
        self._persist_settings()

    def _on_sf_quality_changed(self, _event=None) -> None:
        quality_name = self._settings_sf_quality_var.get()
        quality_code = self._scryfall_quality_map.get(quality_name, "large")
        self._settings.scryfall_quality = quality_code
        self._persist_settings()

    def _on_sf_policy_changed(self, _event=None) -> None:
        policy_name = self._settings_sf_policy_var.get()
        policy_code = self._scryfall_policy_map.get(policy_name, "english")
        self._settings.scryfall_fail_policy = policy_code
        self._persist_settings()

    def _settings_reset(self) -> None:
        self._settings.output_dir = None
        self._settings.cut_line_color = DEFAULT_CUT_LINE_COLOR
        self._settings.cut_line_style = DEFAULT_CUT_LINE_STYLE
        self._settings.cut_line_width = DEFAULT_CUT_LINE_WIDTH
        self._settings.cut_line_over_cards = DEFAULT_CUT_LINE_OVER_CARDS
        self._settings.cut_line_over_fronts = DEFAULT_CUT_LINE_OVER_FRONTS
        self._settings.cut_line_over_backs = DEFAULT_CUT_LINE_OVER_BACKS

        self._settings.scryfall_lang = DEFAULT_SCRYFALL_LANG
        self._settings.scryfall_quality = DEFAULT_SCRYFALL_QUALITY
        self._settings.scryfall_fail_policy = DEFAULT_SCRYFALL_FAIL_POLICY

        self._custom_output_dir = None
        self.out_dir_var.set(str(output_dir()))
        self._settings_out_var.set(str(output_dir()))
        self._settings_color_var.set(DEFAULT_CUT_LINE_COLOR)
        self._color_swatch.configure(bg=DEFAULT_CUT_LINE_COLOR)
        self._settings_style_var.set(DEFAULT_CUT_LINE_STYLE)
        self._settings_width_var.set(f"{DEFAULT_CUT_LINE_WIDTH:.1f}")
        self._settings_over_fronts_var.set(DEFAULT_CUT_LINE_OVER_FRONTS)
        self._settings_over_backs_var.set(DEFAULT_CUT_LINE_OVER_BACKS)
        self._over_cards_sub_frame.pack_forget()

        self._settings_sf_lang_var.set(self._scryfall_lang_inv_map.get(DEFAULT_SCRYFALL_LANG))
        self._settings_sf_quality_var.set(self._scryfall_quality_inv_map.get(DEFAULT_SCRYFALL_QUALITY))
        self._settings_sf_policy_var.set(self._scryfall_policy_inv_map.get(DEFAULT_SCRYFALL_FAIL_POLICY))
        self._update_sf_policy_state()

        self._persist_settings()

    def _persist_settings(self) -> None:
        save_settings(self._settings, app_base_dir())
