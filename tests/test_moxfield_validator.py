import tkinter as tk
from unittest.mock import MagicMock, patch

from gui.moxfield_validator import ValidatorApp
from src.deck_importer import DeckCard


def _mock_json_resp(json_data, status=200):
    resp = MagicMock()
    resp.json.return_value = json_data
    resp.raise_for_status.return_value = None
    resp.content = b"IMAGEDATA"
    resp.status_code = status
    return resp


class TestCardValidationLogic:
    def test_validate_card_normal_valid(self):
        card = DeckCard("Black Lotus", "vma", "4", 1, "main")
        mock_data = {
            "name": "Black Lotus",
            "set": "vma",
            "collector_number": "4",
            "highres_image": True,
            "digital": False,
            "rarity": "mythic",
            "type_line": "Artifact",
            "image_uris": {
                "small": "https://cards.scryfall.io/small/front/0/0/abc.jpg"
            }
        }
        
        # Test ValidatorApp validation method directly without tkinter loop
        root = tk.Tk()
        try:
            app = ValidatorApp(root)
            with patch("gui.moxfield_validator._fetch_raw_card_data", return_value=mock_data):
                res = app._validate_single_card(card)
                
            assert res.status == "🟢 Válido"
            assert res.details == "Todo correcto"
            assert res.front_url == "https://cards.scryfall.io/small/front/0/0/abc.jpg"
            assert res.back_url is None
        finally:
            root.destroy()

    def test_validate_card_missing_set_resolved_by_name(self):
        card = DeckCard("Black Lotus", "", "", 1, "main")
        mock_data = {
            "name": "Black Lotus",
            "set": "vma",
            "collector_number": "4",
            "highres_image": True,
            "digital": False,
            "rarity": "mythic",
            "type_line": "Artifact",
            "image_uris": {
                "small": "https://cards.scryfall.io/small/front/0/0/abc.jpg"
            }
        }
        
        root = tk.Tk()
        try:
            app = ValidatorApp(root)
            with patch("gui.moxfield_validator.fetch_card_by_name", return_value=("vma", "4")), \
                 patch("gui.moxfield_validator._fetch_raw_card_data", return_value=mock_data):
                res = app._validate_single_card(card)
                
            assert res.status == "⚠️ Advertencia"
            assert "Edición/Nº faltante" in res.details
            assert res.set_code == "vma"
            assert res.collector_number == "4"
        finally:
            root.destroy()

    def test_validate_card_warning_not_highres(self):
        card = DeckCard("Black Lotus", "vma", "4", 1, "main")
        mock_data = {
            "name": "Black Lotus",
            "set": "vma",
            "collector_number": "4",
            "highres_image": False,
            "digital": False,
            "rarity": "mythic",
            "type_line": "Artifact",
            "image_uris": {
                "small": "https://cards.scryfall.io/small/front/0/0/abc.jpg"
            }
        }
        
        root = tk.Tk()
        try:
            app = ValidatorApp(root)
            with patch("gui.moxfield_validator._fetch_raw_card_data", return_value=mock_data):
                res = app._validate_single_card(card)
                
            assert res.status == "⚠️ Advertencia"
            assert "Imagen de baja resolución" in res.details
        finally:
            root.destroy()

    def test_validate_card_warning_digital(self):
        card = DeckCard("Black Lotus", "vma", "4", 1, "main")
        mock_data = {
            "name": "Black Lotus",
            "set": "vma",
            "collector_number": "4",
            "highres_image": True,
            "digital": True,
            "rarity": "mythic",
            "type_line": "Artifact",
            "image_uris": {
                "small": "https://cards.scryfall.io/small/front/0/0/abc.jpg"
            }
        }
        
        root = tk.Tk()
        try:
            app = ValidatorApp(root)
            with patch("gui.moxfield_validator._fetch_raw_card_data", return_value=mock_data):
                res = app._validate_single_card(card)
                
            assert res.status == "⚠️ Advertencia"
            assert "Carta digital" in res.details
        finally:
            root.destroy()

    def test_validate_card_mdfc(self):
        card = DeckCard("Birgi, God of Storytelling", "khm", "123", 1, "main")
        mock_data = {
            "name": "Birgi, God of Storytelling",
            "set": "khm",
            "collector_number": "123",
            "highres_image": True,
            "digital": False,
            "rarity": "rare",
            "type_line": "Legendary Creature / Legendary Artifact",
            "card_faces": [
                {"image_uris": {"small": "https://cards.scryfall.io/small/front/1/2/birgi.jpg"}},
                {"image_uris": {"small": "https://cards.scryfall.io/small/back/1/2/horn.jpg"}}
            ]
        }
        
        root = tk.Tk()
        try:
            app = ValidatorApp(root)
            with patch("gui.moxfield_validator._fetch_raw_card_data", return_value=mock_data):
                res = app._validate_single_card(card)
                
            assert res.status == "🟢 Válido"
            assert "Doble Cara (MDFC)" in res.details
            assert res.front_url == "https://cards.scryfall.io/small/front/1/2/birgi.jpg"
            assert res.back_url == "https://cards.scryfall.io/small/back/1/2/horn.jpg"
        finally:
            root.destroy()

    def test_validate_card_error_not_found(self):
        card = DeckCard("Black Lotus", "vma", "999", 1, "main")
        
        root = tk.Tk()
        try:
            app = ValidatorApp(root)
            with patch("gui.moxfield_validator._fetch_raw_card_data", side_effect=Exception("Not found")):
                res = app._validate_single_card(card)
                
            assert res.status == "🔴 Error"
            assert "Error en Scryfall" in res.details
        finally:
            root.destroy()

    def test_validate_token_layout_valid(self):
        card = DeckCard("Zombie Token", "unf", "1", 1, "token")
        mock_data = {
            "name": "Zombie Token",
            "set": "unf",
            "collector_number": "1",
            "highres_image": True,
            "digital": False,
            "rarity": "common",
            "type_line": "Token Creature - Zombie",
            "layout": "token",
            "image_uris": {
                "small": "https://cards.scryfall.io/small/front/0/0/abc.jpg"
            }
        }
        
        root = tk.Tk()
        try:
            app = ValidatorApp(root)
            with patch("gui.moxfield_validator._fetch_raw_card_data", return_value=mock_data):
                res = app._validate_single_card(card)
                
            assert res.status == "🟢 Válido"
            assert "Todo correcto" in res.details
        finally:
            root.destroy()

    def test_validate_token_layout_invalid(self):
        card = DeckCard("Zombie Token", "unf", "1", 1, "token")
        mock_data = {
            "name": "Zombie Token",
            "set": "unf",
            "collector_number": "1",
            "highres_image": True,
            "digital": False,
            "rarity": "common",
            "type_line": "Token Creature - Zombie",
            "layout": "normal",
            "image_uris": {
                "small": "https://cards.scryfall.io/small/front/0/0/abc.jpg"
            }
        }
        
        root = tk.Tk()
        try:
            app = ValidatorApp(root)
            with patch("gui.moxfield_validator._fetch_raw_card_data", return_value=mock_data):
                res = app._validate_single_card(card)
                
            assert res.status == "⚠️ Advertencia"
            assert "Se omitirá al descargar" in res.details
        finally:
            root.destroy()
