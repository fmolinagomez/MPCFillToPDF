"""Tests for gui/main.py — state management and logic without rendering.

Tkinter is instantiated headlessly (window withdrawn).  Tests are skipped
automatically when no display is available (e.g. headless CI).
"""

import tkinter as tk

import pytest

from tests.conftest import make_rgb_image, make_xml

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def tk_root():
    """One Tk root per test module — creating/destroying it per-test is slow."""
    try:
        root = tk.Tk()
        root.withdraw()
        yield root
        root.destroy()
    except tk.TclError:
        pytest.skip("No display available — Tkinter tests skipped")


@pytest.fixture
def app(tk_root):
    """Fresh App instance; GUI state is reset by the fixture."""
    from gui.main import App

    a = App(tk_root)
    yield a
    # Clean up widgets added during the test
    for w in tk_root.winfo_children():
        try:
            w.destroy()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# _build_crop_map
# ---------------------------------------------------------------------------


class TestBuildCropMap:
    def test_empty_returns_empty(self, app):
        assert app._build_crop_map() == {}

    def test_back_crop_true(self, app, tmp_path):
        p = make_rgb_image(tmp_path / "back.jpg")
        app.state.local_backs.append(p)
        app.state.local_back_crop.append(True)
        m = app._build_crop_map()
        assert m.get(p) is True

    def test_back_crop_false(self, app, tmp_path):
        p = make_rgb_image(tmp_path / "back.jpg")
        app.state.local_backs.append(p)
        app.state.local_back_crop.append(False)
        m = app._build_crop_map()
        assert m.get(p) is False

    def test_front_overrides_back_for_same_path(self, app, tmp_path):
        """When a path appears as both back and front, front's setting wins."""
        p = make_rgb_image(tmp_path / "shared.jpg")
        app.state.local_backs.append(p)
        app.state.local_back_crop.append(True)
        app.state.local_fronts.append(p)
        app.state.front_back_paths.append(p)
        app.state.local_front_crop.append(False)
        assert app._build_crop_map()[p] is False

    def test_multiple_items(self, app, tmp_path):
        for i in range(3):
            p = make_rgb_image(tmp_path / f"img{i}.jpg")
            app.state.local_fronts.append(p)
            app.state.front_back_paths.append(None)
            app.state.local_front_crop.append(bool(i % 2))
        m = app._build_crop_map()
        fronts = app.state.local_fronts
        assert m[fronts[0]] is False
        assert m[fronts[1]] is True
        assert m[fronts[2]] is False


# ---------------------------------------------------------------------------
# _resolve_extra_backs
# ---------------------------------------------------------------------------


class TestResolveExtraBacks:
    def test_no_fronts_returns_empty(self, app):
        assert app._resolve_extra_backs() == []

    def test_explicit_back(self, app, tmp_path):
        back = make_rgb_image(tmp_path / "back.jpg")
        app.state.local_fronts.append(make_rgb_image(tmp_path / "front.jpg"))
        app.state.front_back_paths.append(back)
        assert app._resolve_extra_backs() == [back]

    def test_none_assignment_passes_through(self, app, tmp_path):
        app.state.local_fronts.append(make_rgb_image(tmp_path / "front.jpg"))
        app.state.front_back_paths.append(None)
        assert app._resolve_extra_backs() == [None]

    def test_mixed_assignments(self, app, tmp_path):
        back = make_rgb_image(tmp_path / "back.jpg")
        for i in range(3):
            app.state.local_fronts.append(make_rgb_image(tmp_path / f"f{i}.jpg"))
            app.state.front_back_paths.append(back if i == 1 else None)
        result = app._resolve_extra_backs()
        assert result == [None, back, None]


# ---------------------------------------------------------------------------
# _refresh_generate_state
# ---------------------------------------------------------------------------


class TestGenerateState:
    def test_disabled_when_nothing_loaded(self, app):
        app._refresh_generate_state()
        assert "disabled" in str(app.soriano_btn.state())
        assert "disabled" in str(app.fronts_only_btn.state())

    def test_enabled_with_xml(self, app, tmp_path):
        xml = make_xml(
            tmp_path / "t.xml",
            fronts=[{"id": "F1", "name": "C1", "slots": "0"}],
        )
        app.state.xml_paths.append(xml)
        app.running = False
        app._refresh_generate_state()
        assert "disabled" not in str(app.soriano_btn.state())

    def test_disabled_fronts_alone_no_backs(self, app, tmp_path):
        app.state.local_fronts.append(make_rgb_image(tmp_path / "f.jpg"))
        app.state.front_back_paths.append(None)
        app.state.local_front_crop.append(False)
        app.running = False
        app._refresh_generate_state()
        assert "disabled" in str(app.soriano_btn.state())

    def test_enabled_fronts_with_backs(self, app, tmp_path):
        front = make_rgb_image(tmp_path / "f.jpg")
        back = make_rgb_image(tmp_path / "b.jpg")
        app.state.local_fronts.append(front)
        app.state.front_back_paths.append(back)
        app.state.local_front_crop.append(False)
        app.state.local_backs.append(back)
        app.state.local_back_crop.append(False)
        app.running = False
        app._refresh_generate_state()
        assert "disabled" not in str(app.soriano_btn.state())

    def test_disabled_while_running(self, app, tmp_path):
        xml = make_xml(
            tmp_path / "t.xml",
            fronts=[{"id": "F1", "name": "C1", "slots": "0"}],
        )
        app.state.xml_paths.append(xml)
        app.running = True
        app._refresh_generate_state()
        assert "disabled" in str(app.soriano_btn.state())


# ---------------------------------------------------------------------------
# Batch crop toggle (_on_front_crop_all / _on_back_crop_all)
# ---------------------------------------------------------------------------


class TestCropAllToggle:
    def test_set_all_fronts_true(self, app, tmp_path):
        for i in range(4):
            app.state.local_fronts.append(make_rgb_image(tmp_path / f"f{i}.jpg"))
            app.state.front_back_paths.append(None)
            app.state.local_front_crop.append(False)
        app._front_crop_all.set(True)
        app._on_front_crop_all()
        assert all(app.state.local_front_crop)

    def test_clear_all_fronts(self, app, tmp_path):
        for i in range(3):
            app.state.local_fronts.append(make_rgb_image(tmp_path / f"f{i}.jpg"))
            app.state.front_back_paths.append(None)
            app.state.local_front_crop.append(True)
        app._front_crop_all.set(False)
        app._on_front_crop_all()
        assert not any(app.state.local_front_crop)

    def test_set_all_backs_true(self, app, tmp_path):
        for i in range(3):
            p = make_rgb_image(tmp_path / f"b{i}.jpg")
            app.state.local_backs.append(p)
            app.state.local_back_crop.append(False)
        app._back_crop_all.set(True)
        app._on_back_crop_all()
        assert all(app.state.local_back_crop)

    def test_clear_all_backs(self, app, tmp_path):
        for i in range(3):
            p = make_rgb_image(tmp_path / f"b{i}.jpg")
            app.state.local_backs.append(p)
            app.state.local_back_crop.append(True)
        app._back_crop_all.set(False)
        app._on_back_crop_all()
        assert not any(app.state.local_back_crop)

    def test_toggle_empty_list_is_noop(self, app):
        app._front_crop_all.set(True)
        app._on_front_crop_all()  # should not raise
        assert app.state.local_front_crop == []


# ---------------------------------------------------------------------------
# Individual crop-change callbacks
# ---------------------------------------------------------------------------


class TestCropChange:
    def test_front_crop_change(self, app, tmp_path):
        app.state.local_fronts.append(make_rgb_image(tmp_path / "f.jpg"))
        app.state.front_back_paths.append(None)
        app.state.local_front_crop.append(False)
        var = tk.BooleanVar(value=True)
        app._on_front_crop_change(0, var)
        assert app.state.local_front_crop[0] is True

    def test_back_crop_change(self, app, tmp_path):
        app.state.local_backs.append(make_rgb_image(tmp_path / "b.jpg"))
        app.state.local_back_crop.append(False)
        var = tk.BooleanVar(value=True)
        app._on_back_crop_change(0, var)
        assert app.state.local_back_crop[0] is True

    def test_crop_change_out_of_range_is_noop(self, app):
        var = tk.BooleanVar(value=True)
        app._on_front_crop_change(99, var)  # no IndexError


# ---------------------------------------------------------------------------
# _on_mtg_back_change
# ---------------------------------------------------------------------------


class TestMtgBackChange:
    def _add_deck(self, app, url="https://moxfield.com/decks/abc"):
        from gui.main import MtgUrlDeck
        from src.deck_importer import DeckCard

        deck = MtgUrlDeck(url=url, cards=[DeckCard("Forest", "m21", "1", 1, "main")])
        app.state.mtg_url_decks.append(deck)
        return deck

    def test_selecting_numbered_back_sets_path(self, app, tmp_path):
        back = make_rgb_image(tmp_path / "b.jpg")
        app.state.local_backs.append(back)
        app.state.local_back_crop.append(False)
        deck = self._add_deck(app)
        var = tk.StringVar(value="1")
        app._on_mtg_back_change(0, var)
        assert deck.back_path == back

    def test_selecting_default_clears_path(self, app, tmp_path):
        back = make_rgb_image(tmp_path / "b.jpg")
        app.state.local_backs.append(back)
        app.state.local_back_crop.append(False)
        deck = self._add_deck(app)
        deck.back_path = back
        var = tk.StringVar(value="default")
        app._on_mtg_back_change(0, var)
        assert deck.back_path is None

    def test_out_of_range_deck_idx_is_noop(self, app):
        var = tk.StringVar(value="1")
        app._on_mtg_back_change(99, var)  # no IndexError

    def test_removed_back_cleared_on_refresh(self, app, tmp_path):
        back = make_rgb_image(tmp_path / "b.jpg")
        app.state.local_backs.append(back)
        app.state.local_back_crop.append(False)
        deck = self._add_deck(app)
        deck.back_path = back
        app.state.local_backs.clear()
        app.state.local_back_crop.clear()
        app._refresh_xml_rows()
        assert deck.back_path is None


# ---------------------------------------------------------------------------
# Token Layout Filtering GUI tests
# ---------------------------------------------------------------------------


class TestMtgUrlDeckActiveCount:
    def test_active_count_filters_invalid_token_layouts(self):
        from gui.main import MtgUrlDeck
        from src.deck_importer import DeckCard
        
        cards = [
            DeckCard("Valid Token", "unf", "1", 1, "token", layout="token"),
            DeckCard("Invalid Token", "unf", "2", 2, "token", layout="normal"),
            DeckCard("Emblem Token", "unf", "3", 1, "token", layout="emblem"),
            DeckCard("Main Card", "m21", "1", 4, "main"),
        ]
        deck = MtgUrlDeck(url="https://moxfield.com/decks/abc", cards=cards, include_tokens=True)
        # Main cards (4) + Valid Token (1) + Emblem Token (1) = 6
        assert deck.active_count == 6

        # If include_tokens is False:
        deck.include_tokens = False
        assert deck.active_count == 4

    def test_active_count_deduplicates_tokens(self):
        from gui.main import MtgUrlDeck
        from src.deck_importer import DeckCard
        
        cards = [
            # Two copies of the exact same token version
            DeckCard("Zombie Token", "unf", "1", 2, "token", layout="token"),
            DeckCard("Zombie Token", "unf", "1", 1, "token", layout="token"),
            # Different art version of the same token name (should count separately)
            DeckCard("Zombie Token", "m21", "2", 1, "token", layout="token"),
            # Main card
            DeckCard("Main Card", "m21", "1", 4, "main"),
        ]
        deck = MtgUrlDeck(url="https://moxfield.com/decks/abc", cards=cards, include_tokens=True)
        # Main cards (4) + Unique Zombie unf/1 (1) + Unique Zombie m21/2 (1) = 6
        assert deck.active_count == 6


class TestGuiHandleDoneWithWarnings:
    def test_done_event_updates_status_var_with_skipped_tokens(self, app):
        from pathlib import Path
        
        event = ("done", [Path("dummy.pdf")], None, Path("out_dir"), "PDF: 1s", 3)
        app._handle(event)
        
        assert "Aviso: 3 token(s) omitidos por layout no válido" in app.status_var.get()
        assert "Listo" in app.status_var.get()

    def test_done_event_no_skipped_tokens(self, app):
        from pathlib import Path
        
        event = ("done", [Path("dummy.pdf")], None, Path("out_dir"), "PDF: 1s", 0)
        app._handle(event)
        
        assert "omitidos" not in app.status_var.get()
        assert "Listo" in app.status_var.get()


class TestScrollableRowsHorizontalScrollbar:
    def test_scrollable_rows_has_horizontal_scrollbar(self, app):
        from tkinter import ttk
        parent = tk.Frame(app.root)
        parent.pack()
        canvas, inner, window_id = app._build_scrollable_rows(parent)
        
        # Create a very wide label inside inner
        dummy = tk.Label(inner, text="A very long label" * 20)
        dummy.pack()
        
        app.root.update_idletasks()
        
        # Make canvas width small to force overflow
        canvas.configure(width=100)
        app.root.update_idletasks()
        
        # Trigger configure event
        canvas.event_generate("<Configure>", width=100, height=200)
        app.root.update_idletasks()
        
        holder = canvas.master
        hsb = None
        for child in holder.winfo_children():
            wcls = child.winfo_class()
            if wcls == "TScrollbar":
                orient = str(child.cget("orient"))
                if orient == "horizontal":
                    hsb = child
                    break
                
        assert hsb is not None
        # Should be gridded because of overflow
        assert bool(hsb.grid_info()) is True
        
        # Now remove the wide label, make the content narrow, and check if it disappears
        dummy.destroy()
        dummy2 = tk.Label(inner, text="Short")
        dummy2.pack()
        app.root.update_idletasks()
        
        # Make canvas very wide to ensure no overflow
        canvas.configure(width=1000)
        app.root.update_idletasks()
        canvas.event_generate("<Configure>", width=1000, height=200)
        app.root.update_idletasks()
        
        # Should no longer be gridded
        assert bool(hsb.grid_info()) is False


class TestSeparatorBackResolution:
    def test_separator_back_always_uses_deck_back_or_default(self, app, tmp_path):
        from unittest.mock import patch, MagicMock
        from pathlib import Path
        from gui.main import MtgUrlDeck
        from src.deck_importer import DeckCard

        # 1. Setup the deck
        # We need a commander card
        commander = DeckCard("Jace", "m21", "1", 1, "main", is_commander=True)
        # We need a normal card
        card1 = DeckCard("Opt", "m21", "2", 4, "main")
        
        # Custom deck back path
        custom_back = tmp_path / "custom_back.jpg"
        custom_back.touch()
        
        deck = MtgUrlDeck(
            url="https://moxfield.com/decks/abc",
            name="My Deck",
            cards=[commander, card1],
            include_separator=True,
            back_path=custom_back,
            format="commander"
        )
        
        app.state.mtg_url_decks = [deck]
        app.state.local_backs = [custom_back]
        
        # Mock scryfall_download to return paths for download
        # (card, front_path, back_path)
        front_jace = tmp_path / "jace_front.jpg"
        front_jace.touch()
        back_jace = tmp_path / "jace_back.jpg"
        back_jace.touch()
        
        front_opt = tmp_path / "opt_front.jpg"
        front_opt.touch()
        
        # Commander has a specific back (back_jace), e.g., if it's double-faced
        dl_results = [
            (commander, front_jace, back_jace),
            (card1, front_opt, None)
        ]
        
        # Keep track of arguments passed to run_locals_only
        captured_args = {}
        def mock_run_locals_only(*args, **kwargs):
            captured_args["extra_fronts"] = args[0]
            captured_args["default_back"] = args[1]
            captured_args["extra_backs"] = kwargs.get("extra_backs")
            return [Path("output.pdf")]

        # Mock dependencies
        with (
            patch("gui.main.work_dir", return_value=tmp_path),
            patch("gui.main.scryfall_download", return_value=dl_results) as mock_dl,
            patch.object(app, "_create_separator_image") as mock_create_sep,
            patch("gui.main.run_locals_only", side_effect=mock_run_locals_only) as mock_run,
            patch("gui.main.resources_dir", return_value=tmp_path)
        ):
            # Create a mock mtg default back file
            mtg_default_back = tmp_path / "backs" / "mtg" / "back.jpg"
            mtg_default_back.parent.mkdir(parents=True, exist_ok=True)
            mtg_default_back.touch()
            
            # Mock _create_separator_image to create the output file so it exists
            def side_effect_create_sep(name, fmt, cards, path):
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                Path(path).touch()
            mock_create_sep.side_effect = side_effect_create_sep

            # Call _work
            app._work(None, None, False, "#000000", "ticks", 0.5, False, False, False)
            
        extra_fronts = captured_args.get("extra_fronts")
        extra_backs = captured_args.get("extra_backs")
        
        assert extra_fronts is not None
        assert extra_backs is not None
        
        # separator_0.jpg should be the first front
        assert "separator_0.jpg" in extra_fronts[0].name
        
        # The separator's back must be the deck's custom back
        assert extra_backs[0] == custom_back

    def test_separator_back_falls_back_to_default_mtg_back(self, app, tmp_path):
        from unittest.mock import patch, MagicMock
        from pathlib import Path
        from gui.main import MtgUrlDeck
        from src.deck_importer import DeckCard

        # 1. Setup the deck
        # We need a commander card
        commander = DeckCard("Jace", "m21", "1", 1, "main", is_commander=True)
        # We need a normal card
        card1 = DeckCard("Opt", "m21", "2", 4, "main")
        
        deck = MtgUrlDeck(
            url="https://moxfield.com/decks/abc",
            name="My Deck",
            cards=[commander, card1],
            include_separator=True,
            back_path=None, # No custom back
            format="commander"
        )
        
        app.state.mtg_url_decks = [deck]
        # No local backs assigned
        app.state.local_backs = []
        
        # Mock scryfall_download to return paths for download
        front_jace = tmp_path / "jace_front.jpg"
        front_jace.touch()
        back_jace = tmp_path / "jace_back.jpg"
        back_jace.touch()
        
        front_opt = tmp_path / "opt_front.jpg"
        front_opt.touch()
        
        dl_results = [
            (commander, front_jace, back_jace),
            (card1, front_opt, None)
        ]
        
        captured_args = {}
        def mock_run_locals_only(*args, **kwargs):
            captured_args["extra_fronts"] = args[0]
            captured_args["default_back"] = args[1]
            captured_args["extra_backs"] = kwargs.get("extra_backs")
            return [Path("output.pdf")]

        # Mock dependencies
        with (
            patch("gui.main.work_dir", return_value=tmp_path),
            patch("gui.main.scryfall_download", return_value=dl_results) as mock_dl,
            patch.object(app, "_create_separator_image") as mock_create_sep,
            patch("gui.main.run_locals_only", side_effect=mock_run_locals_only) as mock_run,
            patch("gui.main.resources_dir", return_value=tmp_path)
        ):
            # Create a mock mtg default back file
            mtg_default_back = tmp_path / "backs" / "mtg" / "back.jpg"
            mtg_default_back.parent.mkdir(parents=True, exist_ok=True)
            mtg_default_back.touch()
            
            def side_effect_create_sep(name, fmt, cards, path):
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                Path(path).touch()
            mock_create_sep.side_effect = side_effect_create_sep

            # Call _work
            app._work(None, None, False, "#000000", "ticks", 0.5, False, False, False)
            
        extra_fronts = captured_args.get("extra_fronts")
        extra_backs = captured_args.get("extra_backs")
        
        assert extra_fronts is not None
        assert extra_backs is not None
        
        # separator_0.jpg should be the first front
        assert "separator_0.jpg" in extra_fronts[0].name
        
        # The separator's back must be the default MTG back, NOT back_jace!
        assert extra_backs[0] == mtg_default_back

    def test_base_name_uses_sanitized_deck_name_when_single_deck(self, app, tmp_path):
        from unittest.mock import patch
        from gui.main import MtgUrlDeck
        from src.deck_importer import DeckCard

        commander = DeckCard("Jace", "m21", "1", 1, "main", is_commander=True)
        deck = MtgUrlDeck(
            url="https://moxfield.com/decks/abc",
            name="My Extremely Long Moxfield Deck: <Nice>",
            cards=[commander],
            include_separator=False,
            back_path=None,
            format="commander"
        )
        
        app.state.mtg_url_decks = [deck]
        
        front_jace = tmp_path / "jace_front.jpg"
        front_jace.touch()
        back_jace = tmp_path / "jace_back.jpg"
        back_jace.touch()
        
        dl_results = [(commander, front_jace, back_jace)]
        
        captured_base_name = []
        def mock_run_locals_only(*args, **kwargs):
            captured_base_name.append(args[3])
            return [Path("output.pdf")]

        with (
            patch("gui.main.work_dir", return_value=tmp_path),
            patch("gui.main.scryfall_download", return_value=dl_results),
            patch("gui.main.run_locals_only", side_effect=mock_run_locals_only),
            patch("gui.main.resources_dir", return_value=tmp_path)
        ):
            mtg_default_back = tmp_path / "backs" / "mtg" / "back.jpg"
            mtg_default_back.parent.mkdir(parents=True, exist_ok=True)
            mtg_default_back.touch()
            
            app._work(None, None, False, "#000000", "ticks", 0.5, False, False, False)
            
        assert len(captured_base_name) == 1
        assert captured_base_name[0] == "My Extremely Long Moxfield Deck_ _Nice_"





