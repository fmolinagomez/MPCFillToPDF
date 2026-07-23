"""Shared utilities for deck scrapers."""

from __future__ import annotations

import re
import sys
from pathlib import Path


def sanitize_filename(name: str) -> str:
    """Sanitize a deck name to be a safe, cross-platform filename."""
    # Replace invalid or problematic characters with underscores: \ / : * ? " < > |
    sanitized = re.sub(r'[\\/*?:"<>|]', "_", name)
    # Strip spaces and dots from both ends
    sanitized = sanitized.strip(". ")
    if not any(c not in " ._" for c in sanitized):
        return "deck"
    return sanitized



def resources_dir() -> Path:
    """Return the project resources directory, handling frozen (.exe) and dev environments."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "resources"
    return Path(__file__).resolve().parent.parent / "resources"


def generate_fallback_back(
    path: Path,
    bg: str,
    border: str,
    size: tuple[int, int] = (480, 670),
) -> Path:
    """Draw a plain colored card-back rectangle and save it to path. Returns path."""
    from PIL import Image, ImageDraw

    W, H = size
    img = Image.new("RGB", (W, H), bg)
    draw = ImageDraw.Draw(img)
    bw = max(6, W // 25)
    draw.rectangle([0, 0, W - 1, H - 1], outline=border, width=bw)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "PNG")
    return path
