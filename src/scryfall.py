"""Scryfall image downloader — fetches card images by set code and collector number."""

from __future__ import annotations

import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from threading import Event

import requests

from src.cancellation import Cancelled
from src.constants import ProgressCallback
from src.deck_importer import DeckCard

_log = logging.getLogger(__name__)

_SCRYFALL_API = "https://api.scryfall.com/cards/{set_code}/{number}"
_SCRYFALL_NAMED_API = "https://api.scryfall.com/cards/named"
_HEADERS = {
    "User-Agent": "MPCFillToPDF/2.0",
    "Accept": "application/json",
}
_TIMEOUT = 20
_RATE_DELAY = 0.1  # Scryfall policy: max 10 req/s
_RETRY_DELAYS = (3, 6)  # backoff between retries on transient errors

SCRYFALL_THREADS = 5

_rate_lock = threading.Lock()
_last_request_time: float = 0.0

_cache: dict[tuple[str, str, str, str, str], ScryfallCard] = {}
_cache_lock = threading.Lock()

_name_cache: dict[str, tuple[str, str]] = {}
_name_cache_lock = threading.Lock()


class ScryfallError(Exception):
    pass


@dataclass
class ScryfallCard:
    front_url: str
    back_url: str | None


def _throttled_get(url: str, params: dict | None = None) -> requests.Response:
    global _last_request_time
    max_attempts = 1 + len(_RETRY_DELAYS)
    last_exc: requests.RequestException | None = None
    for attempt in range(max_attempts):
        with _rate_lock:
            now = time.monotonic()
            wait = _RATE_DELAY - (now - _last_request_time)
            if wait > 0:
                time.sleep(wait)
            _last_request_time = time.monotonic()
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT, params=params)
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_exc = exc
            if attempt < len(_RETRY_DELAYS):
                delay = _RETRY_DELAYS[attempt]
                _log.warning(
                    "Scryfall timeout/conexión, reintentando en %.0fs (intento %d/%d): %s",
                    delay,
                    attempt + 1,
                    max_attempts,
                    exc,
                )
                time.sleep(delay)
                continue
            raise
        if resp.status_code != 429:
            return resp
        retry_after = float(resp.headers.get("Retry-After", 2**attempt))
        _log.warning(
            "Scryfall 429, reintentando en %.1fs (intento %d/%d)",
            retry_after,
            attempt + 1,
            max_attempts,
        )
        time.sleep(retry_after)
    if last_exc is not None:
        raise last_exc
    return resp


def _fetch_raw_card_data(set_code: str, collector_number: str, lang: str | None = None) -> dict:
    """Get raw JSON data from Scryfall API for set/number/lang."""
    if lang and lang.lower() != "en":
        url = f"https://api.scryfall.com/cards/{set_code.lower()}/{collector_number}/{lang.lower()}"
    else:
        url = _SCRYFALL_API.format(set_code=set_code.lower(), number=collector_number)
    resp = _throttled_get(url)
    resp.raise_for_status()
    return resp.json()


def _extract_images(data: dict, quality: str) -> ScryfallCard:
    """Extract front and back URLs from raw card data based on quality (large or png)."""
    q = quality if quality in ("large", "png") else "large"
    if "image_uris" in data:
        front = data["image_uris"].get(q) or data["image_uris"].get("large", "")
        if not front:
            raise ScryfallError("No se encontró imagen de frente en image_uris")
        return ScryfallCard(front, None)
    elif "card_faces" in data:
        faces = data["card_faces"]
        front = faces[0].get("image_uris", {}).get(q) or faces[0].get("image_uris", {}).get("large", "")
        back = faces[1].get("image_uris", {}).get(q) or faces[1].get("image_uris", {}).get("large") if len(faces) > 1 else None
        if not front:
            raise ScryfallError("No se encontró imagen de frente en card_faces")
        return ScryfallCard(front, back)
    else:
        raise ScryfallError("Formato de respuesta inesperado de Scryfall (falta image_uris o card_faces)")


def fetch_card(
    set_code: str,
    collector_number: str,
    lang: str = "en",
    quality: str = "large",
    fail_policy: str = "english",
) -> ScryfallCard:
    """Return front/back image URLs for the given printing from Scryfall."""
    lang = lang.lower().strip()
    quality = quality.lower().strip()
    fail_policy = fail_policy.lower().strip()
    key = (set_code.lower(), collector_number, lang, quality, fail_policy)
    with _cache_lock:
        if key in _cache:
            return _cache[key]

    if lang == "en":
        try:
            data = _fetch_raw_card_data(set_code, collector_number, lang="en")
            result = _extract_images(data, quality)
        except requests.HTTPError as exc:
            raise ScryfallError(
                f"Carta no encontrada en Scryfall: {set_code}/{collector_number} ({exc})"
            ) from exc
        except requests.RequestException as exc:
            raise ScryfallError(
                f"Error de conexión con Scryfall ({set_code}/{collector_number}): {exc}"
            ) from exc
    else:
        try:
            data = _fetch_raw_card_data(set_code, collector_number, lang=lang)
            result = _extract_images(data, quality)
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                _log.info(
                    "Carta no encontrada en idioma %s: %s/%s. Aplicando política: %s",
                    lang, set_code, collector_number, fail_policy
                )
                if fail_policy == "alternative":
                    try:
                        en_data = _fetch_raw_card_data(set_code, collector_number, lang="en")
                        oracle_id = en_data.get("oracle_id")
                        if oracle_id:
                            search_url = "https://api.scryfall.com/cards/search"
                            search_params = {"q": f"oracle_id:{oracle_id} lang:{lang}"}
                            search_resp = _throttled_get(search_url, params=search_params)
                            if search_resp.status_code == 200:
                                search_data = search_resp.json()
                                results = search_data.get("data", [])
                                if results:
                                    result = _extract_images(results[0], quality)
                                    with _cache_lock:
                                        _cache[key] = result
                                    return result
                        result = _extract_images(en_data, quality)
                    except Exception as alt_exc:
                        _log.warning(
                            "Fallo al buscar alternativa en %s para %s/%s (%s). Usando inglés.",
                            lang, set_code, collector_number, alt_exc
                        )
                        try:
                            en_data = _fetch_raw_card_data(set_code, collector_number, lang="en")
                            result = _extract_images(en_data, quality)
                        except Exception as final_exc:
                            raise ScryfallError(
                                f"Error al descargar versión en inglés tras fallo en idioma: {final_exc}"
                            ) from final_exc
                else:
                    try:
                        en_data = _fetch_raw_card_data(set_code, collector_number, lang="en")
                        result = _extract_images(en_data, quality)
                    except Exception as final_exc:
                        raise ScryfallError(
                            f"Error al descargar versión en inglés de {set_code}/{collector_number}: {final_exc}"
                        ) from final_exc
            else:
                raise ScryfallError(
                    f"Error HTTP al consultar Scryfall ({set_code}/{collector_number}/{lang}): {exc}"
                ) from exc
        except requests.RequestException as exc:
            raise ScryfallError(
                f"Error de conexión con Scryfall ({set_code}/{collector_number}/{lang}): {exc}"
            ) from exc

    with _cache_lock:
        _cache[key] = result
    return result


def fetch_card_by_name(name: str) -> tuple[str, str]:
    """Return (set_code, collector_number) for the given card name using Scryfall exact search."""
    key = name.lower()
    with _name_cache_lock:
        if key in _name_cache:
            return _name_cache[key]

    try:
        resp = _throttled_get(_SCRYFALL_NAMED_API, params={"exact": name})
        resp.raise_for_status()
        data = resp.json()
    except requests.HTTPError as exc:
        raise ScryfallError(f"Carta no encontrada en Scryfall: {name} ({exc})") from exc
    except requests.RequestException as exc:
        raise ScryfallError(f"Error de conexión con Scryfall ({name}): {exc}") from exc

    result = (str(data.get("set", "")).lower(), str(data.get("collector_number", "")))
    with _name_cache_lock:
        _name_cache[key] = result
    return result


def _ext_from_url(url: str) -> str:
    m = re.search(r"\.(jpg|jpeg|png|webp)", url, re.IGNORECASE)
    return f".{m.group(1).lower()}" if m else ".jpg"


def download_card_images(
    card: DeckCard,
    dest_dir: Path,
    lang: str = "en",
    quality: str = "large",
    fail_policy: str = "english",
) -> tuple[Path, Path | None]:
    """Download front and (for MDFCs) back images. Returns (front_path, back_path|None)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    set_code = card.set_code
    collector_number = card.collector_number
    if not set_code or not collector_number:
        set_code, collector_number = fetch_card_by_name(card.name)
    scryfall = fetch_card(set_code, collector_number, lang=lang, quality=quality, fail_policy=fail_policy)
    slug = f"{set_code.lower()}_{collector_number}_{lang}_{quality}"

    front_ext = _ext_from_url(scryfall.front_url)
    front_path = dest_dir / f"{slug}{front_ext}"
    if not front_path.exists():
        _log.info("Descargando imagen: %s", slug)
        resp = _throttled_get(scryfall.front_url)
        resp.raise_for_status()
        front_path.write_bytes(resp.content)

    back_path: Path | None = None
    if scryfall.back_url:
        back_ext = _ext_from_url(scryfall.back_url)
        back_path = dest_dir / f"{slug}_back{back_ext}"
        if not back_path.exists():
            _log.info("Descargando reverso MDFC: %s", slug)
            resp = _throttled_get(scryfall.back_url)
            resp.raise_for_status()
            back_path.write_bytes(resp.content)

    return front_path, back_path


def download_deck_images(
    cards: list[DeckCard],
    dest_dir: Path,
    progress_cb: ProgressCallback = None,
    cancel_event: Event | None = None,
    lang: str = "en",
    quality: str = "large",
    fail_policy: str = "english",
) -> list[tuple[DeckCard, Path, Path | None]]:
    """Download images for all unique cards in parallel.

    Returns an ordered list of (card, front_path, back_path|None) matching the input order.
    """
    total = len(cards)
    done = 0
    id_to_result: dict[int, tuple[DeckCard, Path, Path | None]] = {}

    with ThreadPoolExecutor(max_workers=SCRYFALL_THREADS) as executor:
        futures = {
            executor.submit(download_card_images, c, dest_dir, lang, quality, fail_policy): (i, c)
            for i, c in enumerate(cards)
        }
        try:
            for future in as_completed(futures):
                if cancel_event is not None and cancel_event.is_set():
                    for f in futures:
                        f.cancel()
                    raise Cancelled()
                idx, card = futures[future]
                front_path, back_path = future.result()
                id_to_result[idx] = (card, front_path, back_path)
                done += 1
                if progress_cb:
                    progress_cb(done, total)
        except Exception:
            for f in futures:
                f.cancel()
            raise

    return [id_to_result[i] for i in range(len(cards))]
