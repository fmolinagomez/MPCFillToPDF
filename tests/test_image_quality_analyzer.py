import tkinter as tk
from unittest.mock import patch, MagicMock
from pathlib import Path
import pytest
import queue

from src.deck_importer import FetchedDeck, DeckCard
from image_quality_analyzer import AnalyzerApp


@pytest.fixture(scope="module")
def tk_root():
    """One Tk root per test module — skips tests automatically when no display is available."""
    try:
        root = tk.Tk()
        root.withdraw()
        yield root
        root.destroy()
    except tk.TclError:
        pytest.skip("No display available — Tkinter tests skipped")


@pytest.fixture
def app(tk_root):
    """Fresh AnalyzerApp instance; GUI state is reset by the fixture."""
    a = AnalyzerApp(tk_root)
    yield a
    # Clean up widgets added during the test
    for w in tk_root.winfo_children():
        try:
            w.destroy()
        except Exception:
            pass


class TestAnalyzerMoxfield:
    def test_moxfield_button_exists(self, app):
        """Verify the 'Añadir desde Moxfield...' button exists and is correctly configured."""
        assert hasattr(app, "btn_add_moxfield")
        assert app.btn_add_moxfield is not None
        assert app.btn_add_moxfield.cget("text") == "Añadir desde Moxfield..."

    @patch("image_quality_analyzer.fetch_deck")
    @patch("image_quality_analyzer.download_deck_images")
    def test_moxfield_import_success(self, mock_download, mock_fetch, app, tmp_path):
        """Verify the background thread and queue messaging for a successful Moxfield import."""
        # 1. Mock fetch_deck to return a dummy deck
        card1 = DeckCard("Black Lotus", "LEA", "1", 1, "main")
        deck = FetchedDeck("My Deck", [card1])
        mock_fetch.return_value = deck

        # 2. Mock download_deck_images to return dummy paths
        front_path = tmp_path / "lea_1.jpg"
        front_path.touch()
        mock_download.return_value = [(card1, front_path, None)]

        # Run import worker synchronously or test thread queuing
        app._moxfield_import_worker("https://www.moxfield.com/decks/dummy")

        # 3. Verify messages put in the queue
        msgs = []
        while True:
            try:
                msgs.append(app.queue.get_nowait())
            except queue.Empty:
                break

        assert len(msgs) >= 3
        # First message should be progress
        assert msgs[0]["type"] == "download_progress"
        # Then another progress
        assert msgs[1]["type"] == "download_progress"
        # Final message should be done
        assert msgs[-1]["type"] == "download_done"
        assert msgs[-1]["files"] == [front_path]
        assert msgs[-1]["deck_name"] == "My Deck"

        # Now test _check_queue handling
        app.is_processing = True
        app.queue.put(msgs[-1])  # Re-enqueue the done message
        app._check_queue()

        # It should process the done message, add file, and reset state
        assert front_path in app.pending_files
        assert not app.is_processing
        assert app.progress["value"] == 0

    @patch("image_quality_analyzer.fetch_deck")
    def test_moxfield_import_error(self, mock_fetch, app):
        """Verify the background thread and queue messaging handles errors correctly."""
        from src.deck_importer import DeckImportError
        mock_fetch.side_effect = DeckImportError("Moxfield API Error")

        # Run import worker
        app._moxfield_import_worker("https://www.moxfield.com/decks/dummy")

        # Verify error message put in the queue
        msgs = []
        while True:
            try:
                msgs.append(app.queue.get_nowait())
            except queue.Empty:
                break

        assert len(msgs) == 2
        assert msgs[-1]["type"] == "download_error"
        assert "Moxfield API Error" in msgs[-1]["message"]

        # Test _check_queue handling error
        app.is_processing = True
        app.queue.put(msgs[-1])
        app._check_queue()

        # It should reset state
        assert not app.is_processing
        assert app.progress["value"] == 0
