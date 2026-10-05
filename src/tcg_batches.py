"""Adapters that turn imported TCG decks into normalized printable batches."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Event
from typing import Protocol

from src.deck_importer import DeckCard
from src.lorcana_scraper import LocanaDeck, get_lorcana_back
from src.lorcana_scraper import download_images as lorcana_download
from src.lorcana_scraper import expand_deck as lorcana_expand
from src.op_scraper import OPDeck, get_op_backs
from src.op_scraper import download_images as op_download
from src.op_scraper import expand_deck as op_expand
from src.pdf_generator import YUGIOH_LAYOUT
from src.print_batch import PrintBatch
from src.rb_scraper import RBDeck, get_rb_backs
from src.rb_scraper import download_images as rb_download
from src.rb_scraper import expand_deck as rb_expand
from src.scryfall import download_deck_images as scryfall_download
from src.ygo_scraper import YGODeck, get_ygo_back
from src.ygo_scraper import download_images as ygo_download
from src.ygo_scraper import expand_deck as ygo_expand

ProgressReporter = Callable[[str, int, int, str], None]


class MtgDeck(Protocol):
    """GUI-selected Magic deck interface consumed by the Magic adapter."""

    cards: list[DeckCard]
    back_path: Path | None

    def includes(self, card: DeckCard) -> bool: ...


class TcgBatchAdapter(Protocol):
    """Adapter interface for a TCG that can join the standard print run."""

    def prepare(self) -> PrintBatch: ...


@dataclass(frozen=True)
class YugiohBatchAdapter:
    """Normalize Yu-Gi-Oh! decks while preserving their separate print run."""

    decks: tuple[tuple[YGODeck, bool], ...]
    work_dir: Path
    cancel_event: Event | None
    report_progress: ProgressReporter

    def prepare(self) -> PrintBatch:
        if not self.decks:
            return PrintBatch("yugioh_proxies")
        total = sum(len({card.card_id for card in deck.cards}) for deck, _ in self.decks)
        self.report_progress("download", 0, total, "Yu-Gi-Oh!")
        fronts: list[Path] = []
        downloaded = 0
        for deck, include_side in self.decks:
            selected = getattr(deck, "selected_card_ids", None)
            if selected is not None:
                deck = replace(
                    deck, cards=[card for card in deck.cards if card.card_id in selected]
                )
            offset = downloaded

            def _progress(done: int, _total: int, *, _offset=offset) -> None:
                self.report_progress("download", _offset + done, total, "Yu-Gi-Oh!")

            image_map = ygo_download(
                deck,
                self.work_dir / "ygo_raw",
                cancel_event=self.cancel_event,
                progress_cb=_progress,
            )
            fronts.extend(ygo_expand(deck, image_map, include_side))
            downloaded += len({card.card_id for card in deck.cards})
        back = get_ygo_back()
        return PrintBatch(
            "yugioh_proxies",
            fronts,
            [back] * len(fronts),
            {path: False for path in {*fronts, back}},
            back,
            YUGIOH_LAYOUT,
            True,
        )


@dataclass(frozen=True)
class OnePieceBatchAdapter:
    decks: tuple[OPDeck, ...]
    work_dir: Path
    cancel_event: Event | None
    report_progress: ProgressReporter

    def prepare(self) -> PrintBatch:
        if not self.decks:
            return PrintBatch("One Piece")
        label = " + ".join(deck.name for deck in self.decks)
        total = sum(len({card.card_id for card in deck.cards}) for deck in self.decks)
        self.report_progress("download", 0, total, f"One Piece – {label}")
        image_map: dict[str, Path] = {}
        downloaded = 0
        for deck in self.decks:
            selected = getattr(deck, "selected_card_ids", None)
            if selected is not None:
                deck = replace(
                    deck, cards=[card for card in deck.cards if card.card_id in selected]
                )
            offset = downloaded

            def _progress(done: int, _total: int, *, _offset=offset) -> None:
                self.report_progress("download", _offset + done, total, f"One Piece – {label}")

            image_map.update(
                op_download(
                    deck,
                    self.work_dir / "op_raw",
                    cancel_event=self.cancel_event,
                    progress_cb=_progress,
                )
            )
            downloaded += len({card.card_id for card in deck.cards})
        standard_back, leader_back = get_op_backs()
        leader_backs = {
            deck.leader.card_id: leader_back for deck in self.decks if deck.leader is not None
        }
        fronts: list[Path] = []
        backs: list[Path | None] = []
        for deck in self.decks:
            leader = deck.leader
            resolved_leader_back = leader_backs.get(leader.card_id) if leader else None
            deck_fronts, deck_backs = op_expand(
                deck, image_map, resolved_leader_back, standard_back
            )
            fronts.extend(deck_fronts)
            backs.extend(standard_back if back is None else back for back in deck_backs)
        crop_map = {path: False for path in set(fronts) | {standard_back, *leader_backs.values()}}
        return PrintBatch("One Piece", fronts, backs, crop_map, standard_back)


@dataclass(frozen=True)
class RiftboundBatchAdapter:
    decks: tuple[tuple[RBDeck, bool], ...]
    work_dir: Path
    cancel_event: Event | None
    report_progress: ProgressReporter

    def prepare(self) -> PrintBatch:
        if not self.decks:
            return PrintBatch("Riftbound")
        label = " + ".join(deck.name for deck, _ in self.decks)
        total = sum(len({card.variant_id for card in deck.cards}) for deck, _ in self.decks)
        self.report_progress("download", 0, total, f"Riftbound – {label}")
        image_map: dict[str, Path] = {}
        downloaded = 0
        for deck, _ in self.decks:
            selected = getattr(deck, "selected_card_ids", None)
            if selected is not None:
                deck = replace(
                    deck, cards=[card for card in deck.cards if card.variant_id in selected]
                )
            offset = downloaded

            def _progress(done: int, _total: int, *, _offset=offset) -> None:
                self.report_progress("download", _offset + done, total, f"Riftbound – {label}")

            image_map.update(
                rb_download(
                    deck,
                    self.work_dir / "rb_raw",
                    cancel_event=self.cancel_event,
                    progress_cb=_progress,
                )
            )
            downloaded += len({card.variant_id for card in deck.cards})
        back_by_section = get_rb_backs()
        default_back = back_by_section.get("maindeck") or next(iter(back_by_section.values()))
        fronts: list[Path] = []
        backs: list[Path | None] = []
        for deck, include_runes in self.decks:
            deck_fronts, deck_backs = rb_expand(
                deck, image_map, back_by_section, include_runes=include_runes
            )
            fronts.extend(deck_fronts)
            backs.extend(default_back if back is None else back for back in deck_backs)
        crop_map = {path: False for path in set(fronts) | set(back_by_section.values())}
        return PrintBatch("Riftbound", fronts, backs, crop_map, default_back)


@dataclass(frozen=True)
class LorcanaBatchAdapter:
    decks: tuple[LocanaDeck, ...]
    work_dir: Path
    cancel_event: Event | None
    report_progress: ProgressReporter

    def prepare(self) -> PrintBatch:
        if not self.decks:
            return PrintBatch("Lorcana")
        label = " + ".join(deck.name for deck in self.decks)
        total = sum(len({card.card_id for card in deck.cards}) for deck in self.decks)
        self.report_progress("download", 0, total, f"Lorcana – {label}")
        image_map: dict[str, Path] = {}
        downloaded = 0
        for deck in self.decks:
            selected = getattr(deck, "selected_card_ids", None)
            if selected is not None:
                deck = replace(
                    deck, cards=[card for card in deck.cards if card.card_id in selected]
                )
            offset = downloaded

            def _progress(done: int, _total: int, *, _offset=offset) -> None:
                self.report_progress("download", _offset + done, total, f"Lorcana – {label}")

            image_map.update(
                lorcana_download(
                    deck,
                    self.work_dir / "lorcana_raw",
                    cancel_event=self.cancel_event,
                    progress_cb=_progress,
                )
            )
            downloaded += len({card.card_id for card in deck.cards})
        default_back = get_lorcana_back()
        fronts: list[Path] = []
        backs: list[Path | None] = []
        for deck in self.decks:
            deck_fronts, deck_backs = lorcana_expand(deck, image_map)
            fronts.extend(deck_fronts)
            backs.extend(default_back if back is None else back for back in deck_backs)
        crop_map = {path: False for path in set(fronts) | {default_back}}
        return PrintBatch("Lorcana", fronts, backs, crop_map, default_back)


@dataclass(frozen=True)
class MagicBatchAdapter:
    decks: tuple[MtgDeck, ...]
    work_dir: Path
    default_back: Path
    local_backs: tuple[Path, ...]
    cancel_event: Event | None
    report_progress: ProgressReporter
    scryfall_downloader: Callable = scryfall_download
    separator_creator: Callable | None = None
    scryfall_lang: str = "en"
    scryfall_quality: str = "large"
    scryfall_fail_policy: str = "english"
    scryfall_quality_check: bool = True
    scryfall_blur_threshold: float = 100.0
    scryfall_brightness_contrast_check: bool = True
    scryfall_minimize_tokens: bool = False

    def prepare(self) -> PrintBatch:
        if not self.decks:
            return PrintBatch("magic_url")

        # 1. Identify active cards and commanders for each deck
        decks_with_cards = []
        for _deck in self.decks:
            active_cards = []
            seen_tokens = set()
            for c in _deck.cards:
                if not _deck.includes(c) or c.quantity <= 0:
                    continue
                if c.zone == "token":
                    key = ((c.set_code or "").lower(), c.collector_number or "")
                    if key in seen_tokens:
                        continue
                    seen_tokens.add(key)
                    active_cards.append(replace(c, quantity=1))
                else:
                    active_cards.append(c)

            zone_order = {"main": 0, "token": 1, "side": 2}
            active_cards.sort(key=lambda x: (zone_order.get(x.zone, 0), x.name.casefold()))

            commander_card = None
            for c in _deck.cards:
                if getattr(c, "is_commander", False):
                    commander_card = c
                    break
            if not commander_card:
                for c in _deck.cards:
                    if c.zone == "main":
                        commander_card = c
                        break

            decks_with_cards.append(
                {
                    "deck": _deck,
                    "active_cards": active_cards,
                    "commander": commander_card,
                }
            )

        # 2. Collect all cards to download
        mtg_cards_all = []
        for item in decks_with_cards:
            mtg_cards_all.extend(item["active_cards"])
            if item["commander"] and not any(
                c.name == item["commander"].name
                and c.set_code == item["commander"].set_code
                and c.collector_number == item["commander"].collector_number
                for c in item["active_cards"]
            ):
                mtg_cards_all.append(replace(item["commander"], quantity=0))

        label = f"Magic – {len(self.decks)} mazo(s)"
        self.report_progress("download", 0, len(mtg_cards_all), label)

        def _mtg_prog(done: int, total: int) -> None:
            self.report_progress("download", done, total, label)

        scryfall_dir = self.work_dir / "scryfall"
        try:
            dl_results = self.scryfall_downloader(
                mtg_cards_all,
                scryfall_dir,
                _mtg_prog,
                cancel_event=self.cancel_event,
                lang=self.scryfall_lang,
                quality=self.scryfall_quality,
                fail_policy=self.scryfall_fail_policy,
                quality_check=self.scryfall_quality_check,
                blur_threshold=self.scryfall_blur_threshold,
                brightness_contrast_check=self.scryfall_brightness_contrast_check,
            )
        except TypeError:
            dl_results = self.scryfall_downloader(
                mtg_cards_all,
                scryfall_dir,
                _mtg_prog,
                cancel_event=self.cancel_event,
            )

        image_map = {}
        for card, front_path, back_path in dl_results:
            if card.scryfall_id:
                image_map[card.scryfall_id] = (front_path, back_path)
            image_map[(card.set_code.lower(), card.collector_number)] = (front_path, back_path)

        local_back_set = set(self.local_backs)
        all_mtg_back_paths: set[Path] = {self.default_back}
        mtg_fronts: list[Path] = []
        mtg_backs_resolved: list[Path | None] = []

        printed_tokens_global = set()
        for idx, item in enumerate(decks_with_cards):
            _deck = item["deck"]

            if getattr(_deck, "include_separator", False) and self.separator_creator:
                sep_front_path = scryfall_dir / f"separator_{idx}.jpg"
                sep_back_path = self.default_back
                if _deck.back_path in local_back_set:
                    sep_back_path = _deck.back_path

                try:
                    display_name = getattr(_deck, "display_name", getattr(_deck, "name", "Mazo"))
                    deck_format = getattr(_deck, "format", "commander")
                    self.separator_creator(display_name, deck_format, _deck.cards, sep_front_path)
                except Exception as e:
                    import logging

                    logging.getLogger(__name__).error("Error al crear la carta separadora: %s", e)
                    sep_front_path = None

                if sep_front_path and sep_front_path.exists():
                    mtg_fronts.append(sep_front_path)
                    mtg_backs_resolved.append(sep_back_path)
                    if sep_back_path:
                        all_mtg_back_paths.add(sep_back_path)

            for card in item["active_cards"]:
                comp_key = (card.set_code.lower(), card.collector_number)
                front_path, back_path = image_map.get(comp_key, (None, None))
                if front_path is None and card.scryfall_id:
                    front_path, back_path = image_map.get(card.scryfall_id, (None, None))
                if not front_path or not front_path.exists():
                    continue

                if card.zone == "token" and self.scryfall_minimize_tokens:
                    if comp_key in printed_tokens_global:
                        continue
                    printed_tokens_global.add(comp_key)

                if back_path is not None:
                    resolved_back = back_path
                elif _deck.back_path in local_back_set:
                    resolved_back = _deck.back_path
                else:
                    resolved_back = self.default_back

                all_mtg_back_paths.add(resolved_back)

                for _ in range(card.quantity):
                    mtg_fronts.append(front_path)
                    mtg_backs_resolved.append(resolved_back)

        crop_map = {
            p: False for p in set(mtg_fronts) | all_mtg_back_paths if p not in local_back_set
        }
        return PrintBatch("magic_url", mtg_fronts, mtg_backs_resolved, crop_map, self.default_back)
