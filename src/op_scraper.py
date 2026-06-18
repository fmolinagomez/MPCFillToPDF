"""One Piece Card Game deck scraper for onepiece.gg (via dotgg API)."""
from __future__ import annotations

import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import requests
from PIL import Image, ImageDraw

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
    )
}
_DECK_API  = "https://api.dotgg.gg/cgfw/getdeck?game=onepiece&slug={slug}"
_CARDS_API = "https://api.dotgg.gg/cgfw/getcards?game=onepiece&mode=indexed"
_IMAGE_URL = "https://static.dotgg.gg/onepiece/card/{card_id}.webp"

def _resources_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "resources"
    return Path(__file__).resolve().parent.parent / "resources"


def get_op_backs() -> tuple[Path, Path]:
    """Return (default_back, leader_back) from resources/backs/op/.

    Falls back to generating simple colored images if the files are missing.
    """
    op_dir = _resources_dir() / "backs" / "op"
    default = op_dir / "default.png"
    leader  = op_dir / "lider.png"
    if default.exists() and leader.exists():
        return default, leader
    return _generate_fallback_backs()


def _generate_fallback_backs() -> tuple[Path, Path]:
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    W, H = 480, 670

    def _make(path: Path, bg: str, border: str) -> Path:
        img = Image.new("RGB", (W, H), bg)
        draw = ImageDraw.Draw(img)
        bw = max(6, W // 25)
        draw.rectangle([0, 0, W - 1, H - 1], outline=border, width=bw)
        img.save(path, "PNG")
        return path

    return (
        _make(tmp / "default.png", "#0A1628", "#B0B8C8"),
        _make(tmp / "lider.png",   "#8B0000", "#CCCCCC"),
    )


_COLOR_HEX: dict[str, str] = {
    "Red":    "#C62828",
    "Blue":   "#1565C0",
    "Green":  "#2E7D32",
    "Purple": "#6A1B9A",
    "Yellow": "#F57F17",
    "Black":  "#1A1A1A",
}
_STANDARD_BACK_BG = "#0A1628"
_STANDARD_BACK_BORDER = "#B0B8C8"


@dataclass
class OPCard:
    card_id: str
    name: str
    quantity: int
    is_leader: bool
    colors: list[str]


@dataclass
class OPDeck:
    name: str
    slug: str
    cards: list[OPCard]

    @property
    def leader(self) -> OPCard | None:
        return next((c for c in self.cards if c.is_leader), None)

    @property
    def total_slots(self) -> int:
        return sum(c.quantity for c in self.cards)


def slug_from_url(url: str) -> str:
    m = re.search(r"/decks/([^/?#]+)", url)
    if not m:
        raise ValueError(f"No se pudo extraer el slug de la URL: {url}")
    return m.group(1).strip("/")


def scrape_deck(url: str) -> OPDeck:
    """Fetch deck metadata from onepiece.gg. Does NOT download images."""
    slug = slug_from_url(url)

    r = requests.get(_DECK_API.format(slug=slug), headers=_HEADERS, timeout=15)
    r.raise_for_status()
    deck_data = r.json()

    r2 = requests.get(_CARDS_API, headers=_HEADERS, timeout=30)
    r2.raise_for_status()
    raw = r2.json()
    names = raw["names"]
    cards_db: dict[str, dict] = {row[0]: dict(zip(names, row)) for row in raw["data"]}

    cards: list[OPCard] = []
    for card_id, qty_str in deck_data["deck"].items():
        meta = cards_db.get(card_id, {})
        is_leader = meta.get("cardType", "").upper() == "LEADER"
        color_str = meta.get("Color", "")
        colors = [c.strip() for c in color_str.split("/") if c.strip()]
        cards.append(OPCard(
            card_id=card_id,
            name=meta.get("name", card_id),
            quantity=int(qty_str),
            is_leader=is_leader,
            colors=colors,
        ))

    return OPDeck(name=deck_data.get("humanname", slug), slug=slug, cards=cards)


def download_images(
    deck: OPDeck,
    dest_dir: Path,
    cancel_event: threading.Event | None = None,
    progress_cb=None,
) -> dict[str, Path]:
    """Download one image per unique card. Returns {card_id: local_path}."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    image_map: dict[str, Path] = {}
    done = 0
    total = len(deck.cards)

    def _fetch(card: OPCard) -> tuple[str, Path]:
        path = dest_dir / f"{card.card_id}.webp"
        if not path.exists():
            r = requests.get(
                _IMAGE_URL.format(card_id=card.card_id),
                headers=_HEADERS, timeout=20,
            )
            r.raise_for_status()
            path.write_bytes(r.content)
        return card.card_id, path

    with ThreadPoolExecutor(max_workers=5) as ex:
        futs = {ex.submit(_fetch, c): c for c in deck.cards}
        for fut in as_completed(futs):
            if cancel_event and cancel_event.is_set():
                break
            card_id, path = fut.result()
            image_map[card_id] = path
            done += 1
            if progress_cb:
                progress_cb(done, total)

    return image_map


def _card_size(reference_image: Path) -> tuple[int, int]:
    with Image.open(reference_image) as img:
        return img.size


def make_leader_back(leader: OPCard, reference_image: Path, dest: Path) -> Path:
    """Generate a colored card back for the leader using its color(s)."""
    w, h = _card_size(reference_image)
    img = Image.new("RGB", (w, h))
    draw = ImageDraw.Draw(img)

    hex_colors = [_COLOR_HEX.get(c, "#333333") for c in leader.colors] or ["#333333"]

    if len(hex_colors) == 1:
        draw.rectangle([0, 0, w, h], fill=hex_colors[0])
    else:
        # Diagonal split for dual-color leaders
        draw.polygon([(0, 0), (w, 0), (0, h)], fill=hex_colors[0])
        draw.polygon([(w, 0), (w, h), (0, h)], fill=hex_colors[1])

    border_px = max(6, w // 25)
    draw.rectangle([0, 0, w - 1, h - 1], outline="#888888", width=border_px)
    inner = border_px * 2
    draw.rectangle([inner, inner, w - 1 - inner, h - 1 - inner], outline="#CCCCCC", width=2)

    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, "WEBP")
    return dest


def make_standard_back(reference_image: Path, dest: Path) -> Path:
    """Generate a neutral dark back for regular OP cards."""
    w, h = _card_size(reference_image)
    img = Image.new("RGB", (w, h), _STANDARD_BACK_BG)
    draw = ImageDraw.Draw(img)
    border_px = max(6, w // 25)
    draw.rectangle([0, 0, w - 1, h - 1], outline=_STANDARD_BACK_BORDER, width=border_px)
    inner = border_px * 2
    draw.rectangle([inner, inner, w - 1 - inner, h - 1 - inner], outline=_STANDARD_BACK_BORDER, width=2)
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, "WEBP")
    return dest


def expand_deck(
    deck: OPDeck,
    image_map: dict[str, Path],
    leader_back: Path | None,
    standard_back: Path,
) -> tuple[list[Path], list[Path | None]]:
    """
    Expand each card by its quantity.
    Returns (fronts, per_slot_backs) where None means use the default (standard) back.
    """
    fronts: list[Path] = []
    backs: list[Path | None] = []
    for card in deck.cards:
        img_path = image_map.get(card.card_id)
        if img_path is None:
            continue
        slot_back: Path | None = leader_back if (card.is_leader and leader_back) else None
        for _ in range(card.quantity):
            fronts.append(img_path)
            backs.append(slot_back)
    return fronts, backs
