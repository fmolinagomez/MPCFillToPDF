"""MPCFillToPDF GUI — pick XML(s) and optional local images, run the pipeline,
open the output folder."""

import logging
import math
import os
import queue
import shutil
import sys
import threading
import time
import tkinter as tk
import traceback
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

try:
    import windnd

    _WINDND_AVAILABLE = True
except ImportError:
    _WINDND_AVAILABLE = False

from gui.locals_tab import LocalsTabMixin
from gui.lorcana_tab import LorcanaTabMixin
from gui.op_tab import OPTabMixin
from gui.paths import app_base_dir, output_dir, work_dir
from gui.rb_tab import RBTabMixin
from gui.settings_tab import SettingsTabMixin
from gui.widgets import (
    APP_TITLE,
    STAGE_LABELS,
    attach_context_menu,
    ellipsize,
    load_tab_icon,
    notify,
)
from gui.xml_tab import XmlTabMixin
from gui.ygo_tab import YGOTabMixin
from src.app_settings import AppSettings, load_settings
from src.cancellation import Cancelled
from src.constants import CARDS_PER_PAGE, Stage
from src.deck_importer import DeckCard
from src.downloader import (
    DownloadPartialError,
    DownloadPermissionError,
    DownloadRateLimitError,
    DownloadTimeoutError,
)
from src.execution_plan import build_combined_print_run, standalone_print_batches
from src.lorcana_scraper import LocanaDeck
from src.op_scraper import OPDeck
from src.parser import CardOrder
from src.pipeline import run_locals_only, run_plan
from src.precheck import (
    XmlReport,
    analyze,
    check_drive_access,
    collect_drive_ids,
    format_warning,
    plan,
    write_manifest,
)
from src.print_batch import PrintBatch
from src.print_run import StandalonePrintRun
from src.rb_scraper import RBDeck
from src.scraper_utils import resources_dir, sanitize_filename
from src.scryfall import download_deck_images as scryfall_download
from src.tcg_batches import (
    LorcanaBatchAdapter,
    MagicBatchAdapter,
    OnePieceBatchAdapter,
    RiftboundBatchAdapter,
    YugiohBatchAdapter,
)
from src.validator import ValidationWarning
from src.worker_events import (
    FileEvent,
    ProgressEvent,
    RunFinishedEvent,
    VerificationWarningEvent,
    normalize_event,
)
from src.ygo_scraper import YGODeck

_log = logging.getLogger(__name__)


@dataclass
class MtgUrlDeck:
    url: str
    cards: list[DeckCard]
    include_side: bool = False
    include_tokens: bool = False
    name: str = ""
    back_path: Path | None = None
    format: str = "commander"
    include_separator: bool = False
    selected_card_ids: set[int] | None = None

    @property
    def display_name(self) -> str:
        if self.name:
            return self.name
        try:
            platform = self.url.split("/")[2].replace("www.", "")
            slug = self.url.rstrip("/").split("/")[-1]
            return f"{platform}/{slug}"
        except IndexError:
            return self.url[:40]

    @property
    def active_count(self) -> int:
        main_side_count = sum(
            c.quantity
            for c in self.cards
            if (self.selected_card_ids is None or id(c) in self.selected_card_ids)
            and (c.zone == "main" or (c.zone == "side" and self.include_side))
        )
        token_count = 0
        if self.include_tokens:
            seen_tokens = set()
            for c in self.cards:
                if (self.selected_card_ids is None or id(c) in self.selected_card_ids) and (
                    c.zone == "token"
                    and (not c.layout or c.layout in ("token", "emblem", "double_faced_token"))
                ):
                    key = ((c.set_code or "").lower(), c.collector_number or "")
                    if key not in seen_tokens:
                        seen_tokens.add(key)
                        token_count += 1
        base = main_side_count + token_count
        if self.include_separator:
            base += 1
        return base

    def includes(self, card: DeckCard) -> bool:
        if self.selected_card_ids is not None and id(card) not in self.selected_card_ids:
            return False
        if card.zone == "main":
            return True
        if card.zone == "side":
            return self.include_side
        if card.zone == "token":
            return self.include_tokens and (
                not card.layout or card.layout in ("token", "emblem", "double_faced_token")
            )
        return False


@dataclass
class AppState:
    xml_paths: list[Path] = field(default_factory=list)
    local_fronts: list[Path] = field(default_factory=list)
    local_backs: list[Path] = field(default_factory=list)
    front_back_paths: list[Path | None] = field(default_factory=list)
    local_front_crop: list[bool] = field(default_factory=list)
    local_back_crop: list[bool] = field(default_factory=list)
    mtg_url_decks: list[MtgUrlDeck] = field(default_factory=list)


@dataclass(frozen=True)
class ExecutionRequest:
    """Snapshot of GUI state consumed by the worker without touching Tkinter."""

    plan: object | None
    reports: tuple[XmlReport, ...]
    fronts_only: bool
    keep_cache: bool
    local_fronts: tuple[Path, ...]
    local_backs: tuple[Path, ...]
    extra_backs: tuple[Path | None, ...]
    crop_map: dict[Path, bool]
    op_decks: tuple[OPDeck, ...]
    rb_decks: tuple[tuple[RBDeck, bool], ...]
    lorcana_decks: tuple[LocanaDeck, ...]
    ygo_decks: tuple[tuple[YGODeck, bool], ...]
    mtg_decks: tuple[MtgUrlDeck, ...]
    cut_line_color: str
    cut_line_style: str
    cut_line_width: float
    cut_line_over_cards: bool
    cut_line_over_fronts: bool
    cut_line_over_backs: bool
    max_pdf_bytes: int


class App(
    XmlTabMixin,
    OPTabMixin,
    RBTabMixin,
    LorcanaTabMixin,
    YGOTabMixin,
    LocalsTabMixin,
    SettingsTabMixin,
):
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title(APP_TITLE)
        root.geometry("1200x760")
        root.minsize(1000, 660)

        self.state = AppState()
        self._xml_rows: list[dict] = []
        self._xml_card_counts: dict[Path, int] = {}
        self._xml_orders: dict[Path, CardOrder] = {}
        self._xml_validations: dict[Path, list[ValidationWarning]] = {}
        self._mtg_deck_rows: list[dict] = []
        self._front_rows: list[dict] = []
        self._back_rows: list[dict] = []

        self._settings: AppSettings = load_settings(app_base_dir())
        self._custom_output_dir: Path | None = self._settings.output_dir
        self._cut_line_skip_date: str | None = None

        self.events: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.cancel_event = threading.Event()
        self.running = False
        self.keep_cache = tk.BooleanVar(value=False)
        self._dl_speed_str: str = ""

        self._op_decks: list[OPDeck] = []
        self._op_deck_rows: list[dict] = []

        self._rb_decks: list[RBDeck] = []
        self._rb_deck_rows: list[dict] = []

        self._lorcana_decks: list[LocanaDeck] = []
        self._lorcana_deck_rows: list[dict] = []

        self._ygo_decks: list[YGODeck] = []
        self._ygo_deck_rows: list[dict] = []

        self._build_ui()
        self.root.after(80, self._drain_events)
        self.root.after(200, self._setup_dnd)

    def _build_ui(self) -> None:
        pad = {"padx": 10, "pady": 6}
        frm = ttk.Frame(self.root)
        frm.pack(fill=tk.BOTH, expand=True, **pad)

        bottom_controls = ttk.Frame(frm)
        bottom_controls.pack(side=tk.BOTTOM, fill=tk.X, pady=(10, 0))

        self.keep_cache_cb = ttk.Checkbutton(
            bottom_controls,
            text="Guardar en el PC las imágenes entre ejecuciones",
            variable=self.keep_cache,
        )
        self.keep_cache_cb.pack(anchor=tk.W)

        ttk.Separator(bottom_controls, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=8)

        self.soriano_btn = ttk.Button(
            bottom_controls,
            text="Generar PDF con traseras (Para copisteria, con espejo horizontal)",
            command=lambda: self._start(fronts_only=False),
        )
        self.soriano_btn.pack(fill=tk.X)
        self.soriano_btn.state(["disabled"])

        self.fronts_only_btn = ttk.Button(
            bottom_controls,
            text="Generar PDF solo frontales",
            command=lambda: self._start(fronts_only=True),
        )
        self.fronts_only_btn.pack(fill=tk.X, pady=(4, 0))
        self.fronts_only_btn.state(["disabled"])

        self.stop_btn = ttk.Button(bottom_controls, text="Detener", command=self._request_stop)

        self.status_var = tk.StringVar(value="Listo. Selecciona uno o más XML o imágenes locales.")
        ttk.Label(bottom_controls, textvariable=self.status_var, anchor=tk.W).pack(
            fill=tk.X, pady=(10, 2)
        )

        self.progress = ttk.Progressbar(bottom_controls, mode="determinate", maximum=100)
        self.progress.pack(fill=tk.X)

        out_row = ttk.Frame(bottom_controls)
        out_row.pack(fill=tk.X, pady=(8, 0))
        self.out_dir_var = tk.StringVar(value=str(self._custom_output_dir or output_dir()))
        self.out_label = ttk.Label(
            out_row, textvariable=self.out_dir_var, foreground="#666", anchor=tk.W
        )
        self.out_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.timing_var = tk.StringVar(value="")
        ttk.Label(
            bottom_controls,
            textvariable=self.timing_var,
            foreground="#888",
            anchor=tk.W,
        ).pack(fill=tk.X)

        self._preflight_frame = ttk.LabelFrame(bottom_controls, text="Resumen")
        self._preflight_inner = ttk.Frame(self._preflight_frame)
        self._preflight_inner.pack(fill=tk.X, padx=6, pady=4)
        self._preflight_labels: list[ttk.Label] = []

        top = ttk.Frame(frm)
        top.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        top.columnconfigure(0, weight=1, uniform="cols")
        top.columnconfigure(1, weight=1, uniform="cols")
        top.rowconfigure(0, weight=1)

        self._build_xml_pane(top)
        self._build_locals_pane(top)

    def _build_xml_pane(self, parent: ttk.Frame) -> None:
        notebook = ttk.Notebook(parent)
        notebook.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        self._game_notebook = notebook

        self._mtg_icon_img = load_tab_icon("mtg_icon")
        self._op_icon_img = load_tab_icon("op_icon")
        self._rb_icon_img = load_tab_icon("riftbound_icon")
        self._lorcana_icon_img = load_tab_icon("lorcana_icon")
        self._ygo_icon_img = load_tab_icon("yugioh_icon")

        magic_frame = ttk.Frame(notebook)
        magic_kw: dict = {"text": " Magic"}
        if self._mtg_icon_img:
            magic_kw["image"] = self._mtg_icon_img
            magic_kw["compound"] = "left"
        notebook.add(magic_frame, **magic_kw)
        self._build_magic_tab(magic_frame)

        op_frame = ttk.Frame(notebook)
        op_kw: dict = {"text": " One Piece"}
        if self._op_icon_img:
            op_kw["image"] = self._op_icon_img
            op_kw["compound"] = "left"
        notebook.add(op_frame, **op_kw)
        self._build_onepiece_tab(op_frame)

        rb_frame = ttk.Frame(notebook)
        rb_kw: dict = {"text": " Riftbound"}
        if self._rb_icon_img:
            rb_kw["image"] = self._rb_icon_img
            rb_kw["compound"] = "left"
        notebook.add(rb_frame, **rb_kw)
        self._build_riftbound_tab(rb_frame)

        lorcana_frame = ttk.Frame(notebook)
        lorcana_kw: dict = {"text": " Lorcana"}
        if self._lorcana_icon_img:
            lorcana_kw["image"] = self._lorcana_icon_img
            lorcana_kw["compound"] = "left"
        notebook.add(lorcana_frame, **lorcana_kw)
        self._build_lorcana_tab(lorcana_frame)

        ygo_frame = ttk.Frame(notebook)
        ygo_kw: dict = {"text": " Yu-Gi-Oh!"}
        if self._ygo_icon_img:
            ygo_kw["image"] = self._ygo_icon_img
            ygo_kw["compound"] = "left"
        notebook.add(ygo_frame, **ygo_kw)
        self._build_yugioh_tab(ygo_frame)

        settings_frame = ttk.Frame(notebook)
        notebook.add(settings_frame, text=" ⚙ Configuración")
        self._build_settings_tab(settings_frame)
        self._settings_tab_idx = notebook.index("end") - 1
        self._was_on_settings_tab = False

        notebook.bind("<<NotebookTabChanged>>", self._on_notebook_tab_changed)

    def _on_notebook_tab_changed(self, _event=None) -> None:
        on_settings = self._game_notebook.index("current") == self._settings_tab_idx
        if self._was_on_settings_tab and not on_settings:
            self._save_max_pdf_size_input()
        self._was_on_settings_tab = on_settings
        if on_settings:
            self._locals_pane.grid_remove()
            self._game_notebook.grid_configure(columnspan=2, padx=0)
        else:
            self._game_notebook.grid_configure(columnspan=1, padx=(0, 4))
            self._locals_pane.grid()

    def _build_scrollable_rows(self, parent: ttk.Frame):
        """Create a Canvas + inner Frame for a vertically and horizontally scrolling list of rows."""
        holder = ttk.Frame(parent, relief=tk.SUNKEN, borderwidth=1)
        holder.grid(row=0, column=0, sticky="nsew")
        holder.columnconfigure(0, weight=1)
        holder.rowconfigure(0, weight=1)
        canvas = tk.Canvas(holder, highlightthickness=0, background="#ffffff")
        vsb = ttk.Scrollbar(holder, orient=tk.VERTICAL, command=canvas.yview)
        hsb = ttk.Scrollbar(holder, orient=tk.HORIZONTAL, command=canvas.xview)
        canvas.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")

        inner = ttk.Frame(canvas)
        window_id = canvas.create_window((0, 0), window=inner, anchor="nw")

        def _update_hsb_visibility(canvas_width=None):
            if canvas_width is None:
                canvas.update_idletasks()
                canvas_width = canvas.winfo_width()
            inner_width = inner.winfo_reqwidth()
            if inner_width > canvas_width:
                hsb.grid(row=1, column=0, sticky="ew")
            else:
                hsb.grid_remove()

        def _on_inner_configure(_e):
            canvas.configure(scrollregion=canvas.bbox("all"))
            _update_hsb_visibility()

        def _on_canvas_configure(e):
            req_width = inner.winfo_reqwidth()
            if req_width < e.width:
                canvas.itemconfigure(window_id, width=e.width)
            else:
                canvas.itemconfigure(window_id, width=0)
            _update_hsb_visibility(e.width)

        inner.bind("<Configure>", _on_inner_configure)
        canvas.bind("<Configure>", _on_canvas_configure)
        return canvas, inner, window_id

    @staticmethod
    def _decode_drop(files) -> list[Path]:
        result = []
        for f in files:
            if isinstance(f, bytes):
                try:
                    f = f.decode(sys.getfilesystemencoding())
                except UnicodeDecodeError:
                    f = f.decode("cp1252", errors="replace")
            result.append(Path(str(f)))
        return result

    def _setup_dnd(self) -> None:
        if not _WINDND_AVAILABLE:
            return
        try:
            for w in (self._xml_drop_frame, self.xml_canvas, self.xml_inner):
                windnd.hook_dropfiles(w, func=self._on_drop_xmls)
            for w in (self.backs_canvas, self.backs_inner):
                windnd.hook_dropfiles(w, func=self._on_drop_backs)
            for w in (self.fronts_canvas, self.fronts_inner):
                windnd.hook_dropfiles(w, func=self._on_drop_fronts)
        except Exception:
            pass

    def _bind_mousewheel(self, canvas: tk.Canvas, on: bool) -> None:
        if on:
            self._active_scroll_canvas = canvas
            canvas.bind_all("<MouseWheel>", self._on_mousewheel)
            canvas.bind_all("<Button-4>", self._on_mousewheel)
            canvas.bind_all("<Button-5>", self._on_mousewheel)
        else:
            canvas.unbind_all("<MouseWheel>")
            canvas.unbind_all("<Button-4>")
            canvas.unbind_all("<Button-5>")
            self._active_scroll_canvas = None

    def _on_mousewheel(self, event) -> None:
        canvas = getattr(self, "_active_scroll_canvas", None)
        if canvas is None:
            return
        if event.num == 4:
            canvas.yview_scroll(-1, "units")
        elif event.num == 5:
            canvas.yview_scroll(1, "units")
        else:
            canvas.yview_scroll(int(-event.delta / 120), "units")

    def _effective_output_dir(self) -> Path:
        d = self._custom_output_dir or output_dir()
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _extra_card_count(self) -> int:
        """Return cards appended to the last XML PDF job."""
        return (
            len(self.state.local_fronts)
            + sum(deck.total_slots for deck in self._op_decks)
            + sum(deck.total_slots for deck in self._rb_decks)
            + sum(deck.total_slots for deck in self._lorcana_decks)
            + sum(deck.active_count for deck in self.state.mtg_url_decks)
        )

    def _loaded_xml_plan(self):
        """Build the current XML plan from already parsed orders, if complete."""
        reports: list[XmlReport] = []
        for path in self.state.xml_paths:
            order = self._xml_orders.get(path)
            if order is None:
                return None
            cards = sum(len(card.slots) for card in order.fronts)
            remainder = cards % CARDS_PER_PAGE
            reports.append(XmlReport(path, cards, (CARDS_PER_PAGE - remainder) if remainder else 0))
        return plan(reports, local_count=self._extra_card_count())

    def _update_preflight(self) -> None:
        for widget in self._preflight_labels:
            widget.destroy()
        self._preflight_labels.clear()

        if (
            not self.state.xml_paths
            and not self.state.local_fronts
            and not self._op_decks
            and not self._rb_decks
            and not self._lorcana_decks
            and not self._ygo_decks
            and not self.state.mtg_url_decks
        ):
            self._preflight_frame.pack_forget()
            return

        def _row(parts: list[tuple[str, str]]) -> None:
            frame = ttk.Frame(self._preflight_inner)
            frame.pack(fill=tk.X)
            self._preflight_labels.append(frame)
            for text, style in parts:
                if style == "warn":
                    kw: dict = {"foreground": "#cc0000", "font": ("Segoe UI", 10, "bold")}
                elif style == "bold":
                    kw = {"foreground": "#444", "font": ("Segoe UI", 10, "bold")}
                else:
                    kw = {"foreground": "#444"}
                ttk.Label(frame, text=text, anchor=tk.W, **kw).pack(side=tk.LEFT)

        xml_total = 0

        for p in self.state.xml_paths:
            order = self._xml_orders.get(p)
            if order is None:
                _row([(f"• {p.name}: (no se pudo leer)", "normal")])
                continue
            n = sum(len(card.slots) for card in order.fronts)
            xml_total += n
            _row(
                [
                    (f"• {p.name}: ", "normal"),
                    (f"{n}", "bold"),
                    (" cartas", "normal"),
                ]
            )

        for deck in self._op_decks:
            n = deck.total_slots
            xml_total += n
            _row(
                [
                    (f"• One Piece – {ellipsize(deck.name, 22)}: ", "normal"),
                    (f"{n}", "bold"),
                    (" cartas", "normal"),
                ]
            )

        for deck in self._rb_decks:
            n = deck.total_slots
            xml_total += n
            _row(
                [
                    (f"• Riftbound – {ellipsize(deck.name, 22)}: ", "normal"),
                    (f"{n}", "bold"),
                    (" cartas", "normal"),
                ]
            )

        for deck in self._lorcana_decks:
            n = deck.total_slots
            xml_total += n
            _row(
                [
                    (f"• Lorcana – {ellipsize(deck.name, 22)}: ", "normal"),
                    (f"{n}", "bold"),
                    (" cartas", "normal"),
                ]
            )

        for deck, row in zip(self._ygo_decks, self._ygo_deck_rows):
            n = deck.total_slots(row["include_side_var"].get())
            _row(
                [
                    (f"• Yu-Gi-Oh! – {ellipsize(deck.name, 22)}: ", "normal"),
                    (f"{n}", "bold"),
                    (" cartas (PDF separado)", "normal"),
                ]
            )

        for deck in self.state.mtg_url_decks:
            n = deck.active_count
            xml_total += n
            _row(
                [
                    (f"• Magic – {ellipsize(deck.display_name, 28)}: ", "normal"),
                    (f"{n}", "bold"),
                    (" cartas", "normal"),
                ]
            )

        local_count = len(self.state.local_fronts)
        if local_count:
            _row(
                [
                    ("• Locales: ", "normal"),
                    (f"{local_count}", "bold"),
                    (" carta(s)", "normal"),
                ]
            )

        total_cards = xml_total + local_count
        if total_cards:
            bled_dir = work_dir() / "bled"
            cached = len([f for f in bled_dir.iterdir() if f.is_file()]) if bled_dir.exists() else 0
            cache_str = f" · {cached} imagen(es) en caché" if cached else ""

            xml_plan = self._loaded_xml_plan() if self.state.xml_paths else None
            if xml_plan is not None:
                _row(
                    [
                        ("Plan XML: ", "normal"),
                        (f"{len(xml_plan.jobs)} PDF(s)", "bold"),
                        ("; las cartas adicionales se añaden al último PDF.", "normal"),
                    ]
                )
                for job in xml_plan.jobs:
                    if job.is_merged:
                        names = ", ".join(path.name for path in job.xml_paths)
                        _row([(f"  ↳ Fusión: {names} → {job.total_cards} cartas", "bold")])
                    elif job.extra_locals:
                        _row(
                            [
                                (
                                    f"  ↳ {job.display_name}: {job.cards} XML + "
                                    f"{job.extra_locals} adicionales → {job.total_cards} cartas",
                                    "normal",
                                )
                            ]
                        )
                    else:
                        _row([(f"  ↳ {job.display_name}: {job.total_cards} cartas", "normal")])
                    if job.has_blanks:
                        gap = "hueco" if job.blanks == 1 else "huecos"
                        _row([(f"     ⚠ {job.blanks} {gap} en blanco", "warn")])
                blanks = sum(job.blanks for job in xml_plan.jobs)
                pairs = sum(math.ceil(job.total_cards / CARDS_PER_PAGE) for job in xml_plan.jobs)
            else:
                pairs = math.ceil(total_cards / CARDS_PER_PAGE)
                rem = total_cards % CARDS_PER_PAGE
                blanks = (CARDS_PER_PAGE - rem) if rem else 0

            if blanks:
                _row(
                    [
                        ("Total: ", "normal"),
                        (f"{total_cards}", "bold"),
                        (f" cartas · {pairs} par(es) de páginas{cache_str} · ", "normal"),
                        (f"⚠ {blanks} huecos en la última página", "warn"),
                    ]
                )
            else:
                _row(
                    [
                        ("Total: ", "normal"),
                        (f"{total_cards}", "bold"),
                        (f" cartas · {pairs} par(es) de páginas{cache_str}", "normal"),
                    ]
                )

        self._preflight_frame.pack(fill=tk.X, pady=(8, 0))

    def _refresh_generate_state(self) -> None:
        ready = (
            bool(self.state.xml_paths)
            or (self.state.local_fronts and self.state.local_backs)
            or self._op_decks
            or self._rb_decks
            or self._lorcana_decks
            or self._ygo_decks
            or bool(self.state.mtg_url_decks)
        )
        if ready and not self.running:
            self.soriano_btn.state(["!disabled"])
            self.fronts_only_btn.state(["!disabled"])
        else:
            self.soriano_btn.state(["disabled"])
            self.fronts_only_btn.state(["disabled"])
        self._update_preflight()

    def _resolve_extra_backs(self) -> list[Path | None]:
        return [
            self.state.front_back_paths[i] if i < len(self.state.front_back_paths) else None
            for i in range(len(self.state.local_fronts))
        ]

    def _build_crop_map(self) -> dict[Path, bool]:
        m: dict[Path, bool] = {}
        for p, c in zip(self.state.local_backs, self.state.local_back_crop):
            m[p] = c
        for p, c in zip(self.state.local_fronts, self.state.local_front_crop):
            m[p] = c
        return m

    def _build_execution_request(
        self, plan_: object | None, reports: list[XmlReport], fronts_only: bool
    ) -> ExecutionRequest:
        """Capture every worker input while running on the Tkinter thread."""
        return ExecutionRequest(
            plan=plan_,
            reports=tuple(reports),
            fronts_only=fronts_only,
            keep_cache=bool(self.keep_cache.get()),
            local_fronts=tuple(self.state.local_fronts),
            local_backs=tuple(self.state.local_backs),
            extra_backs=tuple(self._resolve_extra_backs()),
            crop_map=self._build_crop_map(),
            op_decks=tuple(self._op_decks),
            rb_decks=tuple(
                (deck, bool(row["print_runes_var"].get()))
                for deck, row in zip(self._rb_decks, self._rb_deck_rows)
            ),
            lorcana_decks=tuple(self._lorcana_decks),
            ygo_decks=tuple(
                (deck, bool(row["include_side_var"].get()))
                for deck, row in zip(self._ygo_decks, self._ygo_deck_rows)
            ),
            mtg_decks=tuple(replace(deck) for deck in self.state.mtg_url_decks),
            cut_line_color=self._settings.cut_line_color,
            cut_line_style=self._settings.cut_line_style,
            cut_line_width=self._settings.cut_line_width,
            cut_line_over_cards=self._settings.cut_line_over_cards,
            cut_line_over_fronts=self._settings.cut_line_over_fronts,
            cut_line_over_backs=self._settings.cut_line_over_backs,
            max_pdf_bytes=self._settings.max_pdf_size_mb * 1_000_000,
        )

    def _confirm_cut_lines(self) -> bool:
        """Show a modal summary of cut-line settings before generating.

        Returns True if the user accepted (or already suppressed for today).
        """
        today = datetime.now().date().isoformat()
        if self._cut_line_skip_date == today:
            return True

        s = self._settings
        style_label = (
            "Marcas de margen  (para copistería)"
            if s.cut_line_style == "ticks"
            else "Líneas completas  (para autocorte)"
        )
        if s.cut_line_style == "full":
            over_parts = []
            if s.cut_line_over_fronts:
                over_parts.append("Frontal")
            if s.cut_line_over_backs:
                over_parts.append("Trasero")
            over_label = ", ".join(over_parts) if over_parts else "Ninguna"
        else:
            over_label = None

        accepted = [False]
        skip_var = tk.BooleanVar(value=False)

        dlg = tk.Toplevel(self.root)
        dlg.title("Configuración de líneas de corte")
        dlg.resizable(False, False)
        dlg.transient(self.root)
        dlg.grab_set()

        pad = {"padx": 16, "pady": 6}
        outer = ttk.Frame(dlg)
        outer.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        ttk.Label(
            outer,
            text="Configuración actual de las líneas de corte:",
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor=tk.W, **pad)

        grid = ttk.Frame(outer)
        grid.pack(anchor=tk.W, padx=16, pady=(0, 4))

        def _row(label: str, value: str, color: str | None = None) -> None:
            r = grid.grid_size()[1]
            ttk.Label(grid, text=label, foreground="#555").grid(
                row=r, column=0, sticky=tk.W, pady=2
            )
            if color:
                swatch = tk.Label(grid, width=3, bg=color, relief=tk.SOLID, borderwidth=1)
                swatch.grid(row=r, column=1, sticky=tk.W, padx=(8, 4))
                ttk.Label(grid, text=value, foreground="#222").grid(row=r, column=2, sticky=tk.W)
            else:
                ttk.Label(grid, text=value, foreground="#222").grid(
                    row=r, column=1, columnspan=2, sticky=tk.W, padx=(8, 0)
                )

        _row("Color:", s.cut_line_color, color=s.cut_line_color)
        _row("Grosor:", f"{s.cut_line_width:.1f} pt")
        _row("Estilo:", style_label)
        if over_label is not None:
            _row("Páginas con líneas:", over_label)

        ttk.Separator(outer, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=16, pady=(8, 6))

        ttk.Checkbutton(
            outer,
            text="No volver a preguntar hoy",
            variable=skip_var,
        ).pack(anchor=tk.W, padx=16, pady=(0, 8))

        btn_row = ttk.Frame(outer)
        btn_row.pack(fill=tk.X, padx=16, pady=(0, 10))

        def _accept():
            accepted[0] = True
            if skip_var.get():
                self._cut_line_skip_date = today
            dlg.destroy()

        def _cancel():
            dlg.destroy()

        ttk.Button(btn_row, text="Cancelar", command=_cancel).pack(side=tk.RIGHT, padx=(4, 0))
        ttk.Button(btn_row, text="Aceptar", command=_accept).pack(side=tk.RIGHT)

        dlg.update_idletasks()
        w, h = dlg.winfo_reqwidth(), dlg.winfo_reqheight()
        x = self.root.winfo_x() + (self.root.winfo_width() - w) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - h) // 2
        dlg.geometry(f"{w}x{h}+{x}+{y}")

        dlg.bind("<Return>", lambda _e: _accept())
        dlg.bind("<Escape>", lambda _e: _cancel())
        self.root.wait_window(dlg)
        return accepted[0]

    def _start(self, fronts_only: bool = False) -> None:
        if self.running:
            return

        if (
            not self.state.xml_paths
            and not self.state.local_fronts
            and not self._op_decks
            and not self._rb_decks
            and not self._lorcana_decks
            and not self._ygo_decks
            and not self.state.mtg_url_decks
        ):
            messagebox.showerror(
                APP_TITLE,
                "Selecciona al menos un XML, imágenes locales, o un mazo de "
                "One Piece, Riftbound, Lorcana o desde una URL.",
            )
            return

        if not self.state.xml_paths and self.state.local_fronts and not self.state.local_backs:
            messagebox.showerror(
                APP_TITLE,
                "Sin XMLs se necesita al menos un back local "
                "(el primero actúa como reverso por defecto).",
            )
            return

        if not self._confirm_cut_lines():
            return

        reports = []
        plan_ = None
        if self.state.xml_paths:
            all_xml_warnings = [
                (p, ws) for p in self.state.xml_paths if (ws := self._xml_validations.get(p))
            ]
            if all_xml_warnings:
                lines = []
                for p, ws in all_xml_warnings:
                    for w in ws:
                        lines.append(f"[{p.name}] {w.message}")
                preview = "\n".join(f"• {line}" for line in lines[:10])
                more = f"\n… y {len(lines) - 10} más" if len(lines) > 10 else ""
                if not messagebox.askyesno(
                    APP_TITLE,
                    f"Se encontraron {len(lines)} advertencia(s) en los XML:\n\n"
                    f"{preview}{more}\n\n¿Continuar de todos modos?",
                    icon=messagebox.WARNING,
                ):
                    return

            try:
                reports = analyze(self.state.xml_paths)
            except Exception as e:
                self._show_error_dialog(f"No se pudo analizar el XML:\n{e}")
                return

            plan_ = plan(reports, local_count=self._extra_card_count())

            warning = format_warning(plan_)
            if warning and not messagebox.askyesno(
                APP_TITLE,
                warning + "\n\n¿Continuar de todos modos?",
                icon=messagebox.WARNING,
            ):
                return
        else:
            total = (
                len(self.state.local_fronts)
                + sum(d.total_slots for d in self._op_decks)
                + sum(d.total_slots for d in self._rb_decks)
                + sum(d.total_slots for d in self._lorcana_decks)
                + sum(
                    deck.total_slots(row["include_side_var"].get())
                    for deck, row in zip(self._ygo_decks, self._ygo_deck_rows)
                )
                + sum(d.active_count for d in self.state.mtg_url_decks)
            )
            rem = total % CARDS_PER_PAGE
            if rem:
                blanks = CARDS_PER_PAGE - rem
                s = "hueco" if blanks == 1 else "huecos"
                warning = (
                    f"Aviso: {total} carta(s) no es múltiplo de 9.\n"
                    f"La última página tendrá {blanks} {s} en blanco "
                    "(la imprenta cobra la página entera aunque no esté llena)."
                    "\n\n¿Continuar de todos modos?"
                )
                if not messagebox.askyesno(APP_TITLE, warning, icon=messagebox.WARNING):
                    return

        self.running = True
        self.cancel_event.clear()
        self._dl_speed_str = ""
        self.timing_var.set("")
        self.soriano_btn.state(["disabled"])
        self.fronts_only_btn.state(["disabled"])
        self.stop_btn.state(["!disabled"])
        self.stop_btn.pack(fill=tk.X, pady=(4, 0), after=self.fronts_only_btn)
        self.progress["value"] = 0
        self.status_var.set("Preparando…")
        self._reset_xml_download_progress()
        request = self._build_execution_request(plan_, reports, fronts_only)
        self.worker = threading.Thread(
            target=self._work,
            args=(request,),
            daemon=True,
        )
        self.worker.start()

    def _request_stop(self) -> None:
        if not self.running:
            return
        if not messagebox.askyesno(
            APP_TITLE,
            "¿Seguro que quieres detener el proceso en curso?",
            icon=messagebox.WARNING,
        ):
            return
        self.cancel_event.set()
        self.stop_btn.state(["disabled"])
        self.status_var.set("Cancelando…")

    def _work(
        self,
        request: ExecutionRequest | object | None = None,
        reports: tuple[XmlReport, ...] | list | None = None,
        fronts_only: bool = False,
        cut_line_color: str | None = None,
        cut_line_style: str | None = None,
        cut_line_width: float | None = None,
        cut_line_over_cards: bool | None = None,
        cut_line_over_fronts: bool | None = None,
        cut_line_over_backs: bool | None = None,
        max_pdf_bytes: int | None = None,
    ) -> None:
        if not isinstance(request, ExecutionRequest):
            plan_ = request
            request = self._build_execution_request(
                plan_,
                reports or (),
                fronts_only,
            )
            overrides = {}
            if cut_line_color is not None:
                overrides["cut_line_color"] = cut_line_color
            if cut_line_style is not None:
                overrides["cut_line_style"] = cut_line_style
            if cut_line_width is not None:
                overrides["cut_line_width"] = cut_line_width
            if cut_line_over_cards is not None:
                overrides["cut_line_over_cards"] = cut_line_over_cards
            if cut_line_over_fronts is not None:
                overrides["cut_line_over_fronts"] = cut_line_over_fronts
            if cut_line_over_backs is not None:
                overrides["cut_line_over_backs"] = cut_line_over_backs
            if max_pdf_bytes is not None:
                overrides["max_pdf_bytes"] = max_pdf_bytes
            if overrides:
                request = replace(request, **overrides)
        plan_ = request.plan
        reports = request.reports
        fronts_only = request.fronts_only
        cut_line_color = request.cut_line_color
        cut_line_style = request.cut_line_style
        cut_line_width = request.cut_line_width
        cut_line_over_cards = request.cut_line_over_cards
        cut_line_over_fronts = request.cut_line_over_fronts
        cut_line_over_backs = request.cut_line_over_backs
        max_pdf_bytes = request.max_pdf_bytes
        ygo_decks = request.ygo_decks
        op_decks = request.op_decks
        rb_decks = request.rb_decks
        lorcana_decks = request.lorcana_decks
        mtg_decks = request.mtg_decks
        local_fronts = request.local_fronts
        local_backs = request.local_backs
        extra_backs = request.extra_backs
        crop_map = request.crop_map
        run_dir = None
        wd = None
        try:
            out = self._effective_output_dir()
            wd = work_dir()

            bled_dir = wd / "bled"
            if bled_dir.exists():
                cached = [f for f in bled_dir.iterdir() if f.is_file()]
                if cached:
                    self.events.put(("checkpoint_found", len(cached)))

            run_dir = out / datetime.now().strftime("%d_%m_%Y_%H-%M-%S")
            run_dir.mkdir(parents=True, exist_ok=True)
            generated: list[Path] = []
            _run_start = time.time()
            _phase_first: dict[str, float] = {}
            _phase_done: dict[str, float] = {}

            def _track(stage: str, done: int, total: int) -> None:
                now = time.time()
                if stage not in _phase_first:
                    _phase_first[stage] = now
                if done == total and total > 0:
                    _phase_done[stage] = now

            def _report_tcg(stage: str, done: int, total: int, label: str) -> None:
                _track(stage, done, total)
                self.events.put(ProgressEvent(stage, done, total, label))

            ygo_batch = YugiohBatchAdapter(ygo_decks, wd, self.cancel_event, _report_tcg).prepare()
            if self.cancel_event.is_set():
                self.events.put(("cancelled", run_dir))
                return
            op_batch = OnePieceBatchAdapter(op_decks, wd, self.cancel_event, _report_tcg).prepare()
            rb_batch = RiftboundBatchAdapter(rb_decks, wd, self.cancel_event, _report_tcg).prepare()
            lorcana_batch = LorcanaBatchAdapter(
                lorcana_decks, wd, self.cancel_event, _report_tcg
            ).prepare()
            mtg_batch = MagicBatchAdapter(
                mtg_decks,
                wd,
                resources_dir() / "backs" / "mtg" / "back.jpg",
                local_backs,
                self.cancel_event,
                _report_tcg,
                scryfall_downloader=scryfall_download,
                separator_creator=self._create_separator_image,
                scryfall_lang=self._settings.scryfall_lang,
                scryfall_quality=self._settings.scryfall_quality,
                scryfall_fail_policy=self._settings.scryfall_fail_policy,
                scryfall_quality_check=self._settings.scryfall_quality_check,
                scryfall_blur_threshold=self._settings.scryfall_blur_threshold,
                scryfall_brightness_contrast_check=self._settings.scryfall_brightness_contrast_check,
                scryfall_minimize_tokens=self._settings.scryfall_minimize_tokens,
            ).prepare()

            if self.cancel_event.is_set():
                self.events.put(("cancelled", run_dir))
                return

            batches = [
                PrintBatch("locales", list(local_fronts), list(extra_backs), dict(crop_map)),
                ygo_batch,
                op_batch,
                rb_batch,
                lorcana_batch,
                mtg_batch,
            ]
            for batch in standalone_print_batches(batches):
                self.events.put(FileEvent(1, 1, batch.name))

                def _standalone_progress(stage, done, total, _label=batch.name):
                    _track(stage, done, total)
                    self.events.put(ProgressEvent(stage, done, total, _label))

                generated.extend(
                    StandalonePrintRun(batch).render(
                        run_dir,
                        wd,
                        _standalone_progress,
                        self.cancel_event,
                        fronts_only=fronts_only,
                        cut_line_color=cut_line_color,
                        cut_line_style=cut_line_style,
                        cut_line_width=cut_line_width,
                        cut_line_over_cards=cut_line_over_cards,
                        cut_line_over_fronts=cut_line_over_fronts,
                        cut_line_over_backs=cut_line_over_backs,
                        max_pdf_bytes=max_pdf_bytes,
                    )
                )
            combined_run = build_combined_print_run(batches)
            if plan_ is None and not combined_run.fronts:
                if not request.keep_cache:
                    self._cleanup_workdir(wd)
                self.events.put(RunFinishedEvent(generated, None, run_dir, ""))
                return

            if plan_ is not None:
                xml_paths_flat = [Path(p) for job in plan_.jobs for p in job.xml_paths]
                all_ids = collect_drive_ids(xml_paths_flat)
                raw_dir = wd / "raw"
                to_check = [
                    (did, name) for did, name in all_ids if not list(raw_dir.glob(f"{did}.*"))
                ]
                if to_check:
                    self.events.put(
                        ("progress", Stage.VERIFY, 0, len(to_check), "XML seleccionados")
                    )

                    def _verify_cb(done, total):
                        _track(Stage.VERIFY, done, total)
                        self.events.put(
                            ProgressEvent(Stage.VERIFY, done, total, "XML seleccionados")
                        )

                    inaccessible = check_drive_access(to_check, _verify_cb)

                    if inaccessible and not self.cancel_event.is_set():
                        confirm_event = threading.Event()
                        self.events.put(VerificationWarningEvent(inaccessible, confirm_event))
                        while not confirm_event.wait(timeout=0.1):
                            if self.cancel_event.is_set():
                                break

                if self.cancel_event.is_set():
                    self.events.put(("cancelled", run_dir))
                    return

                _pdf_label = [""]

                def cb(stage, done, total):
                    _track(stage, done, total)
                    name = _pdf_label[0] if stage == "pdf" else "Todas las imágenes"
                    self.events.put(ProgressEvent(stage, done, total, name))

                def on_job_pdf_start(job_idx, total_jobs, job_name):
                    job = next((j for j in plan_.jobs if j.base_name == job_name), None)
                    label = job_name + (" (fusión)" if job and job.is_merged else "")
                    _pdf_label[0] = label
                    self.events.put(FileEvent(job_idx, total_jobs, label))

                def on_xml_download_progress(xml_name, done, total):
                    self.events.put(("xml_download_progress", xml_name, done, total))

                def on_xml_crop_progress(xml_name, done, total):
                    self.events.put(("xml_crop_progress", xml_name, done, total))

                def on_speed_update(speed_mbps: float, eta_sec: float) -> None:
                    self.events.put(("download_speed", speed_mbps, eta_sec))

                pdfs = run_plan(
                    plan_.jobs,
                    run_dir,
                    wd,
                    cb,
                    cancel_event=self.cancel_event,
                    extra_fronts=combined_run.fronts or None,
                    extra_backs=combined_run.backs or None,
                    local_crop_map=combined_run.crop_map or None,
                    on_job_pdf_start=on_job_pdf_start,
                    on_xml_download_progress=on_xml_download_progress,
                    on_xml_crop_progress=on_xml_crop_progress,
                    fronts_only=fronts_only,
                    on_speed_update=on_speed_update,
                    cut_line_color=cut_line_color,
                    cut_line_style=cut_line_style,
                    cut_line_width=cut_line_width,
                    cut_line_over_cards=cut_line_over_cards,
                    cut_line_over_fronts=cut_line_over_fronts,
                    cut_line_over_backs=cut_line_over_backs,
                    max_pdf_bytes=max_pdf_bytes,
                )
                generated.extend(pdfs)
                manifest = write_manifest(plan_, reports, run_dir)
            else:
                if not combined_run.fronts:
                    raise ValueError("No hay cartas para generar.")
                if local_fronts and local_backs:
                    default_back: Path = local_backs[0]
                elif combined_run.default_back is not None:
                    default_back = combined_run.default_back
                else:
                    raise ValueError("No se encontró reverso por defecto.")
                total_decks = len(mtg_decks) + len(op_decks) + len(rb_decks) + len(lorcana_decks)
                if total_decks == 1 and not local_fronts:
                    if mtg_decks:
                        deck_name = mtg_decks[0].name
                    elif op_decks:
                        deck_name = op_decks[0].name
                    elif rb_decks:
                        deck_name = (
                            rb_decks[0][0].name
                            if isinstance(rb_decks[0], tuple)
                            else rb_decks[0].name
                        )
                    elif lorcana_decks:
                        deck_name = lorcana_decks[0].name
                    else:
                        deck_name = ""

                    if deck_name and deck_name.strip():
                        base = sanitize_filename(deck_name)
                    else:
                        base = "combinado"
                else:
                    parts = [
                        p
                        for p in [
                            "locales" if local_fronts else None,
                            "One Piece" if op_batch.fronts else None,
                            "Riftbound" if rb_batch.fronts else None,
                            "Lorcana" if lorcana_batch.fronts else None,
                            "magic_url" if mtg_batch.fronts else None,
                        ]
                        if p
                    ]
                    base = "_".join(parts) if parts else "combinado"
                self.events.put(FileEvent(1, 1, base))

                def cb(stage, done, total, _label=base):
                    _track(stage, done, total)
                    self.events.put(ProgressEvent(stage, done, total, _label))

                pdfs = run_locals_only(
                    combined_run.fronts,
                    default_back,
                    run_dir,
                    base,
                    wd,
                    cb,
                    cancel_event=self.cancel_event,
                    extra_backs=combined_run.backs,
                    local_crop_map=combined_run.crop_map,
                    fronts_only=fronts_only,
                    cut_line_color=cut_line_color,
                    cut_line_style=cut_line_style,
                    cut_line_width=cut_line_width,
                    cut_line_over_cards=cut_line_over_cards,
                    cut_line_over_fronts=cut_line_over_fronts,
                    cut_line_over_backs=cut_line_over_backs,
                    max_pdf_bytes=max_pdf_bytes,
                )
                generated.extend(pdfs)
                manifest = None

            if not request.keep_cache:
                self._cleanup_workdir(wd)

            def _fmt_dur(sec: float) -> str:
                return f"{int(sec) // 60}m {int(sec) % 60}s" if sec >= 60 else f"{sec:.0f}s"

            timing_parts = []
            for stage in (Stage.VERIFY, Stage.DOWNLOAD, Stage.CROP, Stage.PDF):
                if stage in _phase_first and stage in _phase_done:
                    dur = _phase_done[stage] - _phase_first[stage]
                    label = {
                        Stage.VERIFY: "Verif.",
                        Stage.DOWNLOAD: "Descarga",
                        Stage.CROP: "Recorte",
                        Stage.PDF: "PDF",
                    }.get(stage, stage)
                    timing_parts.append(f"{label}: {_fmt_dur(dur)}")
            total_dur = time.time() - _run_start
            timing_str = "  ".join(timing_parts)
            if timing_str:
                timing_str += f"  Total: {_fmt_dur(total_dur)}"

            skipped_tokens_count = 0
            if mtg_decks:
                for _deck in mtg_decks:
                    if _deck.include_tokens:
                        skipped_tokens_count += sum(
                            c.quantity
                            for c in _deck.cards
                            if c.zone == "token"
                            and c.layout not in ("token", "emblem", "double_faced_token")
                        )

            # Generate tokens.md if we have MTG URL decks with tokens
            if mtg_decks:
                has_any_tokens = False
                for _deck in mtg_decks:
                    if any(c.zone == "token" for c in _deck.cards):
                        has_any_tokens = True
                        break

                if has_any_tokens:
                    md_path = run_dir / "tokens.md"
                    md_lines = ["# Fichas Necesarias por Mazo", ""]
                    for _deck in mtg_decks:
                        md_lines.append(f"## Mazo: {_deck.display_name}")
                        tokens = [c for c in _deck.cards if c.zone == "token"]
                        if not tokens:
                            md_lines.append("No se necesitan fichas.")
                            md_lines.append("")
                            continue

                        # Group tokens of this deck by name, set, cn
                        token_groups = {}
                        for c in tokens:
                            key = (
                                c.name,
                                (c.set_code or "").lower(),
                                c.collector_number or "",
                                c.layout,
                            )
                            token_groups[key] = token_groups.get(key, 0) + c.quantity

                        for (name, set_code, cn, layout), qty in sorted(
                            token_groups.items(), key=lambda x: x[0][0].casefold()
                        ):
                            is_valid = layout in ("token", "emblem", "double_faced_token")
                            is_included = _deck.include_tokens and is_valid
                            inc_str = (
                                "Incluida en el PDF" if is_included else "No incluida en el PDF"
                            )
                            details = []
                            if set_code:
                                details.append(set_code.upper())
                            if cn:
                                details.append(cn)
                            details_str = f" ({' '.join(details)})" if details else ""
                            md_lines.append(f"- {qty}x {name}{details_str} — *{inc_str}*")
                        md_lines.append("")

                    try:
                        md_path.write_text("\n".join(md_lines), encoding="utf-8")
                        _log.info("Archivo tokens.md generado en: %s", md_path)
                    except Exception as e:
                        _log.error("No se pudo escribir tokens.md: %s", e)

            self.events.put(
                RunFinishedEvent(generated, manifest, run_dir, timing_str, skipped_tokens_count)
            )
        except DownloadPartialError as e:
            self.events.put(
                (
                    "partial_download_error",
                    e.permission_errors,
                    e.timeout_errors,
                    e.xml_context,
                    run_dir,
                )
            )
        except DownloadRateLimitError:
            self.events.put(("rate_limit_error", run_dir))
        except DownloadPermissionError as e:
            self.events.put(("permission_error", e.card_name, e.xml_name, e.position, run_dir))
        except DownloadTimeoutError as e:
            self.events.put(("timeout_error", e.card_name, e.xml_name, e.position, run_dir))
        except Cancelled:
            self.events.put(("cancelled", run_dir))
        except Exception as e:
            self.events.put(("error", f"{e}\n\n{traceback.format_exc()}", run_dir))

    @staticmethod
    def _cleanup_workdir(wd: Path) -> None:
        for sub in ("raw", "bled", "op_raw", "rb_raw", "lorcana_raw", "ygo_raw", "scryfall"):
            target = wd / sub
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)

    @staticmethod
    def _cleanup_run_dir(run_dir: Path) -> None:
        if run_dir.exists():
            shutil.rmtree(run_dir, ignore_errors=True)

    @staticmethod
    def _create_separator_image(
        deck_name: str, deck_format: str, cards: list, output_path: Path
    ) -> None:
        from PIL import Image, ImageDraw, ImageFont

        # Standard card size
        w, h = 745, 1040

        # 1. Create a beautiful warm charcoal gradient background
        img = Image.new("RGB", (w, h))
        draw_g = ImageDraw.Draw(img)
        for y in range(h):
            r = int(46 - (46 - 19) * (y / h))
            g = int(40 - (40 - 17) * (y / h))
            b = int(34 - (34 - 16) * (y / h))
            draw_g.line([(0, y), (w, y)], fill=(r, g, b))

        # Create an RGBA overlay for drawing semi-transparent elements
        overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw_ol = ImageDraw.Draw(overlay)

        # Dimensions for plaque
        y0 = 60
        y1 = y0 + 170
        y_center = (y0 + y1) // 2
        x0 = 80
        x1 = w - 80
        cx = w // 2

        r_plaque = int(w * 0.027)
        r_inner = int(w * 0.020)

        # Plaque Shadow
        shadow_offset = int(w * 0.008)
        shadow_rect = [
            x0 - 4 + shadow_offset,
            y0 - 4 + shadow_offset,
            x1 + 4 + shadow_offset,
            y1 + 4 + shadow_offset,
        ]
        draw_ol.rounded_rectangle(shadow_rect, radius=r_plaque, fill=(0, 0, 0, 180))

        # Main Stone Plaque (rounded rectangle)
        stone_color = (60, 55, 50, 255)  # Dark warm grey slate
        inner_stone_color = (74, 69, 64, 255)  # Slightly lighter stone inner panel
        draw_ol.rounded_rectangle([x0, y0, x1, y1], radius=r_plaque, fill=stone_color)
        draw_ol.rounded_rectangle(
            [x0 + 8, y0 + 8, x1 - 8, y1 - 8], radius=r_inner, fill=inner_stone_color
        )

        # Metallic Outer Frame (Bronze/Gold border)
        bronze_color = (42, 34, 21, 255)
        gold_color = (197, 165, 96, 255)
        light_gold = (234, 210, 149, 255)

        w_outer_bronze = max(2, int(w * 0.008))
        w_outer_gold = max(1, int(w * 0.0027))
        w_inner_bronze = max(1, int(w * 0.0054))

        # Draw outer bronze outline
        draw_ol.rounded_rectangle(
            [x0, y0, x1, y1], radius=r_plaque, outline=bronze_color, width=w_outer_bronze
        )
        # Draw gold highlight line
        draw_ol.rounded_rectangle(
            [x0 + 2, y0 + 2, x1 - 2, y1 - 2],
            radius=r_plaque - 2,
            outline=gold_color,
            width=w_outer_gold,
        )
        # Draw inner bronze outline
        draw_ol.rounded_rectangle(
            [x0 + 8, y0 + 8, x1 - 8, y1 - 8],
            radius=r_inner,
            outline=bronze_color,
            width=w_inner_bronze,
        )

        # Corner curls
        corner_r = int(w * 0.033)
        draw_ol.arc(
            [x0 - corner_r, y0 - corner_r, x0 + corner_r, y0 + corner_r],
            90,
            270,
            fill=gold_color,
            width=w_outer_gold,
        )
        draw_ol.arc(
            [x0 - corner_r + 4, y0 - corner_r + 4, x0 + corner_r - 4, y0 + corner_r - 4],
            90,
            270,
            fill=bronze_color,
            width=1,
        )
        draw_ol.arc(
            [x0 - corner_r, y1 - corner_r, x0 + corner_r, y1 + corner_r],
            180,
            360,
            fill=gold_color,
            width=w_outer_gold,
        )
        draw_ol.arc(
            [x1 - corner_r, y0 - corner_r, x1 + corner_r, y0 + corner_r],
            270,
            90,
            fill=gold_color,
            width=w_outer_gold,
        )
        draw_ol.arc(
            [x1 - corner_r, y1 - corner_r, x1 + corner_r, y1 + corner_r],
            0,
            180,
            fill=gold_color,
            width=w_outer_gold,
        )

        # Wavy vine lines
        draw_ol.line([(x0 + 40, y0), (cx - 40, y0)], fill=bronze_color, width=w_inner_bronze)
        draw_ol.line([(cx + 40, y0), (x1 - 40, y0)], fill=bronze_color, width=w_inner_bronze)
        draw_ol.line([(x0 + 40, y1), (cx - 40, y1)], fill=bronze_color, width=w_inner_bronze)
        draw_ol.line([(cx + 40, y1), (x1 - 40, y1)], fill=bronze_color, width=w_inner_bronze)

        # Medallions (8 circles in total, matching reference)
        c_white = (243, 230, 199, 255)
        c_blue = (80, 115, 169, 255)
        c_black = (50, 48, 47, 255)
        c_red = (192, 92, 70, 255)
        c_green = (62, 111, 83, 255)

        r1 = int(w * 0.040)
        r2 = int(w * 0.037)
        r3 = int(w * 0.029)

        medallions = [
            (x0 - 6, y_center, r1, c_red),
            (x1 + 6, y_center, r1, c_green),
            (cx, y0 - 6, r2, c_blue),
            (cx, y1 + 6, r2, c_black),
            (x0 + (cx - x0) // 2, y0 - 3, r3, c_white),
            (cx + (x1 - cx) // 2, y0 - 3, r3, c_black),
            (x0 + (cx - x0) // 2, y1 + 3, r3, c_blue),
            (cx + (x1 - cx) // 2, y1 + 3, r3, c_red),
        ]

        for mx, my, mr, mcolor in medallions:
            draw_ol.ellipse([mx - mr, my - mr, mx + mr, my + mr], fill=bronze_color)
            draw_ol.ellipse(
                [mx - mr + 3, my - mr + 3, mx + mr - 3, my + mr - 3], outline=gold_color, width=2
            )
            draw_ol.ellipse(
                [mx - mr + 6, my - mr + 6, mx + mr - 6, my + mr - 6], fill=(20, 15, 10, 255)
            )
            gem_r = mr - 9
            draw_ol.ellipse([mx - gem_r, my - gem_r, mx + gem_r, my + gem_r], fill=mcolor)
            glint_r = gem_r // 3
            if glint_r > 0:
                draw_ol.ellipse(
                    [mx - glint_r - 2, my - glint_r - 2, mx + glint_r - 2, my + glint_r - 2],
                    fill=(255, 255, 255, 150),
                )

        # Composite the frame onto background
        img_composite = Image.alpha_composite(img.convert("RGBA"), overlay)
        img = img_composite.convert("RGB")
        draw = ImageDraw.Draw(img)

        # Load fonts
        font_title = None
        font_sub = None
        font_stats_header = None
        font_stats_val = None

        title_size = 38
        sub_size = 20
        stats_header_size = 22
        stats_val_size = 26

        title_font_names = [
            "/System/Library/Fonts/Supplemental/Georgia Bold.ttf",
            "Georgia Bold",
            "Arial Bold",
        ]
        sub_font_names = [
            "/System/Library/Fonts/Supplemental/Georgia Italic.ttf",
            "Georgia Italic",
            "Arial Italic",
        ]
        stats_header_names = [
            "/System/Library/Fonts/Supplemental/Georgia Bold.ttf",
            "Georgia Bold",
            "Arial Bold",
        ]
        stats_val_names = ["/System/Library/Fonts/Supplemental/Georgia.ttf", "Georgia", "Arial"]

        for f_name in title_font_names:
            try:
                font_title = ImageFont.truetype(f_name, title_size)
                break
            except OSError:
                continue
        if font_title is None:
            font_title = ImageFont.load_default()

        for f_name in sub_font_names:
            try:
                font_sub = ImageFont.truetype(f_name, sub_size)
                break
            except OSError:
                continue
        if font_sub is None:
            font_sub = ImageFont.load_default()

        for f_name in stats_header_names:
            try:
                font_stats_header = ImageFont.truetype(f_name, stats_header_size)
                break
            except OSError:
                continue
        if font_stats_header is None:
            font_stats_header = ImageFont.load_default()

        for f_name in stats_val_names:
            try:
                font_stats_val = ImageFont.truetype(f_name, stats_val_size)
                break
            except OSError:
                continue
        if font_stats_val is None:
            font_stats_val = ImageFont.load_default()

        # Draw Title (Deck Name)
        title_text = deck_name.upper()
        max_title_w = (x1 - x0) - 40
        while title_size > 14:
            try:
                bbox = draw.textbbox((0, 0), title_text, font=font_title)
                tw = bbox[2] - bbox[0]
                th = bbox[3] - bbox[1]
                y_offset_t = bbox[1]
            except AttributeError:
                tw, th = draw.textsize(title_text, font=font_title)
                y_offset_t = 0
            if tw <= max_title_w:
                break
            title_size -= 2
            for f_name in title_font_names:
                try:
                    font_title = ImageFont.truetype(f_name, title_size)
                    break
                except OSError:
                    continue

        try:
            bbox_t = draw.textbbox((0, 0), title_text, font=font_title)
            tw = bbox_t[2] - bbox_t[0]
            th = bbox_t[3] - bbox_t[1]
            y_offset_t = bbox_t[1]
        except AttributeError:
            tw, th = draw.textsize(title_text, font=font_title)
            y_offset_t = 0

        # Translate and draw format subtitle
        FORMAT_TRANSLATION = {
            "commander": "Commander",
            "standard": "Standard",
            "modern": "Modern",
            "pauper": "Pauper",
            "legacy": "Legacy",
            "vintage": "Vintage",
            "pioneer": "Pioneer",
            "cedh": "cEDH",
        }
        fmt_pretty = FORMAT_TRANSLATION.get(deck_format.lower(), deck_format.title())
        sub_text = f"Mazo de {fmt_pretty}"

        try:
            bbox_s = draw.textbbox((0, 0), sub_text, font=font_sub)
            sw = bbox_s[2] - bbox_s[0]
            sh = bbox_s[3] - bbox_s[1]
            y_offset_s = bbox_s[1]
        except AttributeError:
            sw, sh = draw.textsize(sub_text, font=font_sub)
            y_offset_s = 0

        tx = (w - tw) // 2
        ty = y_center - th - 5 - y_offset_t
        sx = (w - sw) // 2
        sy = y_center + 10 - y_offset_s

        # Title Shadow
        draw.text((tx + 2, ty + 2), title_text, fill=(0, 0, 0), font=font_title)
        draw.text((tx - 1, ty - 1), title_text, fill=(0, 0, 0), font=font_title)
        draw.text((tx + 1, ty - 1), title_text, fill=(0, 0, 0), font=font_title)
        draw.text((tx - 1, ty + 1), title_text, fill=(0, 0, 0), font=font_title)
        draw.text((tx + 1, ty + 1), title_text, fill=(0, 0, 0), font=font_title)
        draw.text((tx, ty), title_text, fill=light_gold[:3], font=font_title)

        # Subtitle Shadow
        draw.text((sx + 1, sy + 1), sub_text, fill=(0, 0, 0), font=font_sub)
        draw.text((sx, sy), sub_text, fill=(218, 203, 169), font=font_sub)

        # Stats & Commanders
        mainboard_cards = [c for c in cards if c.zone == "main"]
        commanders = [c for c in cards if getattr(c, "is_commander", False)]

        num_creatures = sum(
            c.quantity for c in mainboard_cards if "creature" in getattr(c, "type_line", "").lower()
        )
        num_lands = sum(
            c.quantity for c in mainboard_cards if "land" in getattr(c, "type_line", "").lower()
        )
        num_instants = sum(
            c.quantity for c in mainboard_cards if "instant" in getattr(c, "type_line", "").lower()
        )
        num_sorceries = sum(
            c.quantity for c in mainboard_cards if "sorcery" in getattr(c, "type_line", "").lower()
        )
        num_enchantments = sum(
            c.quantity
            for c in mainboard_cards
            if "enchantment" in getattr(c, "type_line", "").lower()
        )

        y_cursor = y1 + 90

        is_commander_format = (deck_format.lower() in ["commander", "cedh"]) or len(commanders) > 0
        if is_commander_format and commanders:
            header_text = "COMANDANTE(S)" if len(commanders) > 1 else "COMANDANTE"
            try:
                bbox_h = draw.textbbox((0, 0), header_text, font=font_stats_header)
                hw = bbox_h[2] - bbox_h[0]
                hh = bbox_h[3] - bbox_h[1]
            except AttributeError:
                hw, hh = draw.textsize(header_text, font=font_stats_header)

            hx = (w - hw) // 2
            draw.text((hx + 1, y_cursor + 1), header_text, fill=(0, 0, 0), font=font_stats_header)
            draw.text((hx, y_cursor), header_text, fill=light_gold[:3], font=font_stats_header)
            y_cursor += hh + 15

            for comm in commanders:
                c_name = comm.name
                try:
                    bbox_n = draw.textbbox((0, 0), c_name, font=font_sub)
                    nw = bbox_n[2] - bbox_n[0]
                    nh = bbox_n[3] - bbox_n[1]
                except AttributeError:
                    nw, nh = draw.textsize(c_name, font=font_sub)

                nx = (w - nw) // 2
                draw.text((nx + 1, y_cursor + 1), c_name, fill=(0, 0, 0), font=font_sub)
                draw.text((nx, y_cursor), c_name, fill=(255, 255, 255), font=font_sub)
                y_cursor += nh + 10

            y_cursor += 15
            sep_w = 260
            draw.line(
                [(cx - sep_w // 2, y_cursor), (cx + sep_w // 2, y_cursor)],
                fill=gold_color[:3],
                width=2,
            )
            draw.polygon(
                [(cx - 6, y_cursor), (cx, y_cursor - 6), (cx + 6, y_cursor), (cx, y_cursor + 6)],
                fill=gold_color[:3],
            )
            y_cursor += 35

        stats_header = "ESTADÍSTICAS DEL MAZO"
        try:
            bbox_sh = draw.textbbox((0, 0), stats_header, font=font_stats_header)
            shw = bbox_sh[2] - bbox_sh[0]
            shh = bbox_sh[3] - bbox_sh[1]
        except AttributeError:
            shw, shh = draw.textsize(stats_header, font=font_stats_header)

        shx = (w - shw) // 2
        draw.text((shx + 1, y_cursor + 1), stats_header, fill=(0, 0, 0), font=font_stats_header)
        draw.text((shx, y_cursor), stats_header, fill=light_gold[:3], font=font_stats_header)
        y_cursor += shh + 30

        stats = [
            ("Criaturas", num_creatures),
            ("Tierras", num_lands),
            ("Instantáneos", num_instants),
            ("Conjuros", num_sorceries),
            ("Encantamientos", num_enchantments),
        ]

        for label, val in stats:
            stat_text = f"{label}: {val}"
            try:
                bbox_v = draw.textbbox((0, 0), stat_text, font=font_stats_val)
                vw = bbox_v[2] - bbox_v[0]
                vh = bbox_v[3] - bbox_v[1]
            except AttributeError:
                vw, vh = draw.textsize(stat_text, font=font_stats_val)

            vx = (w - vw) // 2
            draw.text((vx + 1, y_cursor + 1), stat_text, fill=(0, 0, 0), font=font_stats_val)
            draw.text((vx, y_cursor), stat_text, fill=(240, 235, 225), font=font_stats_val)
            y_cursor += vh + 15

        y_cursor += 20
        sep_w = 160
        draw.line(
            [(cx - sep_w // 2, y_cursor), (cx + sep_w // 2, y_cursor)],
            fill=bronze_color[:3],
            width=2,
        )
        draw.ellipse([cx - 4, y_cursor - 4, cx + 4, y_cursor + 4], fill=gold_color[:3])

        output_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(output_path, "JPEG", quality=95)

    def _drain_events(self) -> None:
        try:
            while True:
                ev = normalize_event(self.events.get_nowait())
                self._handle(ev)
        except queue.Empty:
            pass
        finally:
            self.root.after(80, self._drain_events)

    def _handle(self, ev: tuple) -> None:
        kind = ev[0]
        if kind == "file":
            _, i, n, name = ev
            self.status_var.set(f"[{i}/{n}] {name}")
        elif kind == "progress":
            _, stage, done, total, name = ev
            label = STAGE_LABELS.get(stage, stage)
            pct = (done / total * 100.0) if total else 0
            self.progress["value"] = pct
            speed_info = (
                f" — {self._dl_speed_str}" if stage == "download" and self._dl_speed_str else ""
            )
            self.status_var.set(f"{name} — {label}: {done}/{total}{speed_info}")
        elif kind == "download_speed":
            _, speed_mbps, eta_sec = ev
            mins = int(eta_sec) // 60
            secs = int(eta_sec) % 60
            eta_str = f"{mins}:{secs:02d}" if mins > 0 else f"{secs}s"
            self._dl_speed_str = f"{speed_mbps:.1f} MB/s — ETA {eta_str}"
        elif kind == "checkpoint_found":
            _, count = ev
            self.status_var.set(
                f"Se encontraron {count} imagen(es) en caché de una ejecución anterior. "
                "Retomando desde el crop."
            )
        elif kind == "partial_download_error":
            _, perm_errors, timeout_errors, xml_context, run_dir = ev
            if run_dir is not None:
                self._cleanup_run_dir(run_dir)
            parts = []
            if perm_errors:
                names = ", ".join(f"«{name}»" for _, name in perm_errors[:3])
                more = f" y {len(perm_errors) - 3} más" if len(perm_errors) > 3 else ""
                parts.append(
                    f"{len(perm_errors)} imagen(es) no se pudieron descargar: {names}{more}.\n"
                    "Posibles causas:\n"
                    "  • El archivo en Google Drive solo permite descarga con cuenta de Google "
                    "(«Cualquiera con el enlace» no basta para descarga anónima).\n"
                    "  • Le han quitado los permisos de acceso público.\n"
                    "Pide al creador del proxy que comparta las imágenes como "
                    "«Público en Internet» en Google Drive."
                )
            if timeout_errors:
                names = ", ".join(f"«{name}»" for _, name in timeout_errors[:3])
                more = f" y {len(timeout_errors) - 3} más" if len(timeout_errors) > 3 else ""
                parts.append(
                    f"{len(timeout_errors)} imagen(es) con tiempo de espera agotado: {names}{more}.\n"
                    "Las imágenes descargadas correctamente están en caché. "
                    "Vuelve a intentarlo en unos minutos."
                )
            total_failed = len(perm_errors) + len(timeout_errors)
            self.status_var.set(f"Error: {total_failed} imagen(es) no se pudieron descargar.")
            self._finish_running()
            self._show_error_dialog("\n\n".join(parts))
        elif kind == "done":
            if len(ev) == 6:
                _, pdfs, manifest, run_dir, timing_str, skipped_tokens_count = ev
            else:
                _, pdfs, manifest, run_dir, timing_str = ev
                skipped_tokens_count = 0
            self.progress["value"] = 100
            extra = f" Resumen en {manifest.name}." if manifest else ""
            status_text = f"Listo. {len(pdfs)} PDF(s) generados en {run_dir.name}.{extra}"
            if skipped_tokens_count > 0:
                status_text += (
                    f" (Aviso: {skipped_tokens_count} token(s) omitidos por layout no válido)"
                )
            self.status_var.set(status_text)
            if timing_str:
                self.timing_var.set(f"Tiempos — {timing_str}")
                _log.info("Phase timings: %s", timing_str)
            self._finish_running()
            notify(APP_TITLE, f"¡Listo! {len(pdfs)} PDF(s) generados correctamente.")
            self._open_output_folder(run_dir)
        elif kind == "xml_download_progress":
            _, xml_name, done, total = ev
            self._show_xml_download_progress(xml_name, done, total)
        elif kind == "xml_crop_progress":
            _, xml_name, done, total = ev
            self._show_xml_crop_progress(xml_name, done, total)
        elif kind == "verify_warning":
            _, cards, confirm_event = ev
            names = "\n".join(f"  • {name}" for _, name in cards[:8])
            more = f"\n  … y {len(cards) - 8} más" if len(cards) > 8 else ""
            msg = (
                f"{len(cards)} carta(s) sin acceso público en Google Drive:\n\n"
                f"{names}{more}\n\n"
                "El proceso fallará al intentar descargar estas cartas.\n"
                "¿Deseas continuar de todos modos?"
            )
            if not messagebox.askyesno(APP_TITLE, msg, icon=messagebox.WARNING):
                self.cancel_event.set()
            confirm_event.set()
        elif kind == "cancelled":
            _, run_dir = ev
            if run_dir is not None:
                self._cleanup_run_dir(run_dir)
            self.progress["value"] = 0
            self.status_var.set(
                "Proceso detenido. Las imágenes descargadas se conservan para retomar."
            )
            self._finish_running()
        elif kind == "rate_limit_error":
            _, run_dir = ev
            if run_dir is not None:
                self._cleanup_run_dir(run_dir)
            self.status_var.set("Error: demasiadas descargas en poco tiempo.")
            self._finish_running()
            self._show_error_dialog(
                "Se están intentando descargar demasiadas imágenes en poco tiempo, "
                "espera un rato y vuelve a intentar.\n\n"
                "Por favor selecciona «Guardar en el PC las imágenes entre ejecuciones» "
                "así evitamos volver a descargarlas cada vez."
            )
        elif kind == "permission_error":
            _, card_name, xml_name, position, run_dir = ev
            if run_dir is not None:
                self._cleanup_run_dir(run_dir)
            parts = [f"Se ha fallado descargando «{card_name}»"]
            if xml_name:
                parts[0] += f" en el {xml_name}"
            if position:
                parts[0] += f" que está en la posición {position}."
            else:
                parts[0] += "."
            parts.append(
                "Esto no es un fallo del programa. Posibles causas:\n"
                "  • El archivo en Google Drive solo permite descarga con cuenta de Google "
                "(«Cualquiera con el enlace» no basta para descarga anónima).\n"
                "  • Le han quitado los permisos de acceso público.\n"
                "Pide al creador del proxy que comparta las imágenes como "
                "«Público en Internet» en Google Drive."
            )
            self.status_var.set("Error de descarga.")
            self._finish_running()
            self._show_error_dialog("\n\n".join(parts))
        elif kind == "timeout_error":
            _, card_name, xml_name, position, run_dir = ev
            if run_dir is not None:
                self._cleanup_run_dir(run_dir)
            parts = [f"Se ha agotado el tiempo de espera descargando «{card_name}»"]
            if xml_name:
                parts[0] += f" en el {xml_name}"
            if position:
                parts[0] += f" que está en la posición {position}."
            else:
                parts[0] += "."
            parts.append(
                "La descarga no recibió datos durante 30 segundos y se canceló.\n"
                "Puede ser un problema temporal de Google Drive o de tu conexión.\n"
                "Vuelve a intentarlo en unos minutos."
            )
            self.status_var.set("Error de descarga (tiempo agotado).")
            self._finish_running()
            self._show_error_dialog("\n\n".join(parts))
        elif kind == "op_deck_loaded":
            _, deck = ev
            if not any(d.slug == deck.slug for d in self._op_decks):
                self._op_decks.append(deck)
                self._op_refresh_rows()
                self._refresh_generate_state()
            self._op_url_var.set("")
            self._op_status_var.set(f"Añadido: {deck.name}")
            self._op_load_btn.state(["!disabled"])
        elif kind == "op_deck_error":
            _, msg = ev
            self._op_status_var.set("Error al cargar el mazo.")
            self._op_load_btn.state(["!disabled"])
            self._show_error_dialog(msg)
        elif kind == "rb_deck_loaded":
            _, deck = ev
            if not any(d.deck_id == deck.deck_id for d in self._rb_decks):
                self._rb_decks.append(deck)
                self._rb_refresh_rows()
                self._refresh_generate_state()
            self._rb_url_var.set("")
            self._rb_status_var.set(f"Añadido: {deck.name}")
            self._rb_load_btn.state(["!disabled"])
        elif kind == "rb_deck_error":
            _, msg = ev
            self._rb_status_var.set("Error al cargar el mazo.")
            self._rb_load_btn.state(["!disabled"])
            self._show_error_dialog(msg)
        elif kind == "lorcana_deck_loaded":
            _, deck = ev
            if not any(d.deck_id == deck.deck_id for d in self._lorcana_decks):
                self._lorcana_decks.append(deck)
                self._lorcana_refresh_rows()
                self._refresh_generate_state()
            self._lorcana_url_var.set("")
            self._lorcana_status_var.set(f"Añadido: {deck.name}")
            self._lorcana_load_btn.state(["!disabled"])
        elif kind == "lorcana_deck_error":
            _, msg = ev
            self._lorcana_status_var.set("Error al cargar el mazo.")
            self._lorcana_load_btn.state(["!disabled"])
            self._show_error_dialog(msg)
        elif kind == "ygo_deck_loaded":
            _, deck = ev
            if not any(d.deck_id == deck.deck_id for d in self._ygo_decks):
                self._ygo_decks.append(deck)
                self._ygo_refresh_rows()
                self._refresh_generate_state()
            self._ygo_url_var.set("")
            self._ygo_status_var.set(f"Añadido: {deck.name}")
            self._ygo_load_btn.state(["!disabled"])
        elif kind == "ygo_deck_error":
            _, msg = ev
            self._ygo_status_var.set("Error al cargar el mazo.")
            self._ygo_load_btn.state(["!disabled"])
            self._show_error_dialog(msg)
        elif kind == "mtg_url_loaded":
            _, url, cards, include_side, deck_name, deck_format = ev
            self.state.mtg_url_decks.append(
                MtgUrlDeck(
                    url=url,
                    cards=cards,
                    include_side=self._settings.scryfall_download_sideboard,
                    include_tokens=self._settings.scryfall_download_tokens,
                    name=deck_name,
                    format=deck_format,
                    include_separator=self._settings.scryfall_deck_separators,
                )
            )
            dlg = getattr(self, "_mtg_url_dialog", None)
            if dlg and dlg.winfo_exists():
                dlg.destroy()
            self._mtg_url_dialog = None
            self._refresh_xml_rows()
            self._refresh_generate_state()
        elif kind == "mtg_url_error":
            _, msg = ev
            status_var = getattr(self, "_mtg_url_status_var", None)
            import_btn = getattr(self, "_mtg_import_btn", None)
            if status_var:
                status_var.set(f"✗ {msg}")
            if import_btn:
                try:
                    import_btn.state(["!disabled"])
                except Exception:
                    pass
        elif kind == "error":
            _, msg, run_dir = ev
            if run_dir is not None:
                self._cleanup_run_dir(run_dir)
            self.status_var.set("Error durante la generación. Las imágenes en caché se conservan.")
            self._finish_running()
            self._show_error_dialog(msg)

    def _show_error_dialog(self, message: str) -> None:
        dlg = tk.Toplevel(self.root)
        dlg.title(f"{APP_TITLE} — Error")
        dlg.geometry("600x340")
        dlg.resizable(True, True)
        dlg.grab_set()

        ttk.Label(dlg, text="Error", font=("Segoe UI", 11, "bold"), foreground="#cc0000").pack(
            anchor="w", padx=12, pady=(10, 4)
        )

        frame = ttk.Frame(dlg)
        frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 6))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        txt = tk.Text(
            frame,
            wrap=tk.WORD,
            relief=tk.SUNKEN,
            borderwidth=1,
            font=("Consolas", 9),
            state=tk.NORMAL,
        )
        sb = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        txt.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        txt.insert("1.0", message)
        txt.configure(state=tk.DISABLED)
        attach_context_menu(txt)

        btn_row = ttk.Frame(dlg)
        btn_row.pack(fill=tk.X, padx=12, pady=(0, 10))

        def _copy():
            dlg.clipboard_clear()
            dlg.clipboard_append(message)
            copy_btn.configure(text="✓ Copiado")
            dlg.after(2000, lambda: copy_btn.configure(text="Copiar al portapapeles"))

        copy_btn = ttk.Button(btn_row, text="Copiar al portapapeles", command=_copy)
        copy_btn.pack(side=tk.LEFT)
        ttk.Button(btn_row, text="Cerrar", command=dlg.destroy).pack(side=tk.RIGHT)

    def _finish_running(self) -> None:
        self.running = False
        self.worker = None
        self.stop_btn.pack_forget()
        self.stop_btn.state(["!disabled"])
        self._refresh_generate_state()

    def _open_output_folder(self, path: Path) -> None:
        try:
            os.startfile(str(path))
        except AttributeError:
            import subprocess

            opener = "open" if os.uname().sysname == "Darwin" else "xdg-open"
            subprocess.Popen([opener, str(path)])


def _setup_logging() -> None:
    if getattr(sys, "frozen", False):
        try:
            from gui._build_flags import DEBUG_LOGGING
        except ImportError:
            DEBUG_LOGGING = False
        if not DEBUG_LOGGING:
            return
    wd = work_dir()
    wd.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        handlers=[logging.FileHandler(wd / "gui.log", encoding="utf-8")],
    )


def main() -> None:
    _setup_logging()
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
