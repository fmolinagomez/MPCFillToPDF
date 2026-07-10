from unittest.mock import MagicMock, patch

import pytest

from src.deck_importer import DeckCard
from src.scryfall import (
    ScryfallCard,
    ScryfallError,
    download_deck_images,
    fetch_card,
    fetch_card_by_name,
)

_NORMAL_CARD_JSON = {
    "image_uris": {
        "large": "https://cards.scryfall.io/large/front/0/0/abc.jpg",
    }
}

_MDFC_CARD_JSON = {
    "card_faces": [
        {"image_uris": {"large": "https://cards.scryfall.io/large/front/1/1/face0.jpg"}},
        {"image_uris": {"large": "https://cards.scryfall.io/large/back/1/1/face1.jpg"}},
    ]
}


def _mock_resp(json_data, status=200):
    resp = MagicMock()
    resp.json.return_value = json_data
    resp.raise_for_status.return_value = None
    resp.content = b"IMAGEDATA"
    resp.status_code = status
    return resp


class TestFetchCardNormal:
    def setup_method(self):
        import src.scryfall as sf

        sf._cache.clear()

    def test_returns_front_url_and_no_back(self):
        with patch("src.scryfall._throttled_get", return_value=_mock_resp(_NORMAL_CARD_JSON)):
            card = fetch_card("ltr", "152")
        assert card.front_url == "https://cards.scryfall.io/large/front/0/0/abc.jpg"
        assert card.back_url is None

    def test_result_is_scryfall_card_instance(self):
        with patch("src.scryfall._throttled_get", return_value=_mock_resp(_NORMAL_CARD_JSON)):
            card = fetch_card("m21", "1")
        assert isinstance(card, ScryfallCard)

    def test_raises_scryfall_error_on_http_error(self):
        import requests as req

        err = req.HTTPError(response=MagicMock(status_code=404))
        with patch("src.scryfall._throttled_get", side_effect=err):
            with pytest.raises(ScryfallError, match="no encontrada"):
                fetch_card("bad", "0")


class TestFetchCardMDFC:
    def setup_method(self):
        import src.scryfall as sf

        sf._cache.clear()

    def test_returns_both_face_urls(self):
        with patch("src.scryfall._throttled_get", return_value=_mock_resp(_MDFC_CARD_JSON)):
            card = fetch_card("khm", "200")
        assert "face0" in card.front_url
        assert card.back_url is not None
        assert "face1" in card.back_url

    def test_front_is_card_faces_zero(self):
        with patch("src.scryfall._throttled_get", return_value=_mock_resp(_MDFC_CARD_JSON)):
            card = fetch_card("khm", "200")
        assert card.front_url == _MDFC_CARD_JSON["card_faces"][0]["image_uris"]["large"]
        assert card.back_url == _MDFC_CARD_JSON["card_faces"][1]["image_uris"]["large"]


class TestFetchCardCaching:
    def setup_method(self):
        import src.scryfall as sf

        sf._cache.clear()

    def test_second_call_does_not_hit_network(self):
        with patch(
            "src.scryfall._throttled_get", return_value=_mock_resp(_NORMAL_CARD_JSON)
        ) as mock_get:
            fetch_card("ltr", "10")
            fetch_card("ltr", "10")
        assert mock_get.call_count == 1

    def test_cache_keyed_by_set_and_number(self):
        with patch(
            "src.scryfall._throttled_get", return_value=_mock_resp(_NORMAL_CARD_JSON)
        ) as mock_get:
            fetch_card("ltr", "10")
            fetch_card("m21", "10")
        assert mock_get.call_count == 2


class TestDownloadCardImages:
    def setup_method(self):
        import src.scryfall as sf

        sf._cache.clear()

    def test_file_cache_prevents_redownload(self, tmp_path):
        existing = tmp_path / "ltr_10_en_large.jpg"
        existing.write_bytes(b"cached")
        card = DeckCard("Bolt", "ltr", "10", 1, "main")
        with (
            patch(
                "src.scryfall.fetch_card",
                return_value=ScryfallCard("https://example.com/ltr_10.jpg", None),
            ),
            patch("src.scryfall._throttled_get") as mock_get,
        ):
            from src.scryfall import download_card_images

            front, back = download_card_images(card, tmp_path)
        mock_get.assert_not_called()
        assert front == existing
        assert back is None

    def test_downloads_front_when_missing(self, tmp_path):
        card = DeckCard("Bolt", "ltr", "152", 1, "main")
        with (
            patch(
                "src.scryfall.fetch_card",
                return_value=ScryfallCard("https://example.com/img.jpg", None),
            ),
            patch("src.scryfall._throttled_get", return_value=_mock_resp(_NORMAL_CARD_JSON)),
        ):
            from src.scryfall import download_card_images

            front, back = download_card_images(card, tmp_path)
        assert front.exists()
        assert front.read_bytes() == b"IMAGEDATA"
        assert back is None

    def test_downloads_mdfc_back(self, tmp_path):
        card = DeckCard("Fable", "mid", "141", 1, "main")
        with (
            patch(
                "src.scryfall.fetch_card",
                return_value=ScryfallCard(
                    "https://example.com/front.jpg",
                    "https://example.com/back.jpg",
                ),
            ),
            patch("src.scryfall._throttled_get", return_value=_mock_resp(_MDFC_CARD_JSON)),
        ):
            from src.scryfall import download_card_images

            front, back = download_card_images(card, tmp_path)
        assert back is not None
        assert back.exists()
        assert "_back" in back.name


class TestDownloadDeckImages:
    def setup_method(self):
        import src.scryfall as sf

        sf._cache.clear()

    def test_returns_ordered_results(self, tmp_path):
        cards = [
            DeckCard("A", "m21", "1", 1, "main"),
            DeckCard("B", "m21", "2", 1, "main"),
            DeckCard("C", "m21", "3", 1, "main"),
        ]
        side_effects = [
            (tmp_path / "m21_1.jpg", None),
            (tmp_path / "m21_2.jpg", None),
            (tmp_path / "m21_3.jpg", None),
        ]
        for p, _ in side_effects:
            p.write_bytes(b"x")

        with patch("src.scryfall.download_card_images", side_effect=side_effects):
            results = download_deck_images(cards, tmp_path)

        assert len(results) == 3
        names = [c.name for c, _, _ in results]
        assert names == ["A", "B", "C"]


class TestFetchCardByName:
    def setup_method(self):
        import src.scryfall as sf

        sf._name_cache.clear()

    def test_returns_set_and_collector_number(self):
        data = {"set": "LTR", "collector_number": "152"}
        with patch("src.scryfall._throttled_get", return_value=_mock_resp(data)):
            set_code, cn = fetch_card_by_name("Lightning Bolt")
        assert set_code == "ltr"
        assert cn == "152"

    def test_result_cached_on_second_call(self):
        data = {"set": "M21", "collector_number": "295"}
        with patch("src.scryfall._throttled_get", return_value=_mock_resp(data)) as mock_get:
            fetch_card_by_name("Forest")
            fetch_card_by_name("Forest")
        assert mock_get.call_count == 1

    def test_cache_case_insensitive(self):
        data = {"set": "m21", "collector_number": "295"}
        with patch("src.scryfall._throttled_get", return_value=_mock_resp(data)) as mock_get:
            fetch_card_by_name("Forest")
            fetch_card_by_name("FOREST")
        assert mock_get.call_count == 1

    def test_raises_scryfall_error_on_http_error(self):
        import requests as req

        err = req.HTTPError(response=MagicMock(status_code=404))
        with patch("src.scryfall._throttled_get", side_effect=err):
            with pytest.raises(ScryfallError, match="no encontrada"):
                fetch_card_by_name("Nonexistent Card XYZZY")

    def test_set_code_lowercased(self):
        data = {"set": "LTR", "collector_number": "1"}
        with patch("src.scryfall._throttled_get", return_value=_mock_resp(data)):
            set_code, _ = fetch_card_by_name("Gandalf")
        assert set_code == set_code.lower()


class TestDownloadCardImagesNameResolution:
    def setup_method(self):
        import src.scryfall as sf

        sf._cache.clear()
        sf._name_cache.clear()

    def test_resolves_name_only_card(self, tmp_path):
        card = DeckCard("Lightning Bolt", "", "", 1, "main")
        with (
            patch("src.scryfall.fetch_card_by_name", return_value=("ltr", "152")) as mock_name,
            patch(
                "src.scryfall.fetch_card",
                return_value=ScryfallCard("https://example.com/img.jpg", None),
            ),
            patch("src.scryfall._throttled_get", return_value=_mock_resp(_NORMAL_CARD_JSON)),
        ):
            from src.scryfall import download_card_images

            front, back = download_card_images(card, tmp_path)
        mock_name.assert_called_once_with("Lightning Bolt")
        assert front.exists()
        assert back is None

    def test_skips_name_resolution_when_set_and_number_present(self, tmp_path):
        card = DeckCard("Lightning Bolt", "ltr", "152", 1, "main")
        with (
            patch("src.scryfall.fetch_card_by_name") as mock_name,
            patch(
                "src.scryfall.fetch_card",
                return_value=ScryfallCard("https://example.com/img.jpg", None),
            ),
            patch("src.scryfall._throttled_get", return_value=_mock_resp(_NORMAL_CARD_JSON)),
        ):
            from src.scryfall import download_card_images

            download_card_images(card, tmp_path)
        mock_name.assert_not_called()


class TestFetchCardLanguageAndQuality:
    def setup_method(self):
        import src.scryfall as sf
        sf._cache.clear()

    def test_fetch_card_png_quality(self):
        json_data = {
            "image_uris": {
                "large": "https://cards.scryfall.io/large/front/0/0/abc.jpg",
                "png": "https://cards.scryfall.io/png/front/0/0/abc.png",
            }
        }
        with patch("src.scryfall._throttled_get", return_value=_mock_resp(json_data)) as mock_get:
            card = fetch_card("ltr", "152", quality="png")
        assert card.front_url == "https://cards.scryfall.io/png/front/0/0/abc.png"

    def test_fetch_card_spanish_exists(self):
        json_data = {
            "image_uris": {
                "large": "https://cards.scryfall.io/large/front/0/0/es.jpg",
            }
        }
        with patch("src.scryfall._throttled_get", return_value=_mock_resp(json_data)) as mock_get:
            card = fetch_card("ltr", "152", lang="es")
        assert card.front_url == "https://cards.scryfall.io/large/front/0/0/es.jpg"
        mock_get.assert_called_once_with("https://api.scryfall.com/cards/ltr/152/es")

    def test_fetch_card_spanish_missing_fallback_english(self):
        import requests as req
        err_404 = req.HTTPError(response=MagicMock(status_code=404))

        def side_effect(url, params=None):
            if "/es" in url:
                raise err_404
            return _mock_resp(_NORMAL_CARD_JSON)

        with patch("src.scryfall._throttled_get", side_effect=side_effect) as mock_get:
            card = fetch_card("ltr", "152", lang="es", fail_policy="english")

        assert card.front_url == "https://cards.scryfall.io/large/front/0/0/abc.jpg"
        assert mock_get.call_count == 2

    def test_fetch_card_spanish_missing_fallback_alternative_found(self):
        import requests as req
        err_404 = req.HTTPError(response=MagicMock(status_code=404))

        en_json = {
            "oracle_id": "dummy-uuid",
            "image_uris": {
                "large": "https://cards.scryfall.io/large/front/0/0/en.jpg",
            }
        }
        search_json = {
            "data": [
                {
                    "image_uris": {
                        "large": "https://cards.scryfall.io/large/front/0/0/alt_es.jpg",
                    }
                }
            ]
        }

        def side_effect(url, params=None):
            if "/es" in url:
                raise err_404
            elif "cards/search" in url:
                assert params == {"q": "oracle_id:dummy-uuid lang:es"}
                return _mock_resp(search_json)
            else:
                return _mock_resp(en_json)

        with patch("src.scryfall._throttled_get", side_effect=side_effect) as mock_get:
            card = fetch_card("ltr", "152", lang="es", fail_policy="alternative")

        assert card.front_url == "https://cards.scryfall.io/large/front/0/0/alt_es.jpg"
        assert mock_get.call_count == 3

    def test_fetch_card_spanish_missing_fallback_alternative_not_found(self):
        import requests as req
        err_404 = req.HTTPError(response=MagicMock(status_code=404))

        en_json = {
            "oracle_id": "dummy-uuid",
            "image_uris": {
                "large": "https://cards.scryfall.io/large/front/0/0/en.jpg",
            }
        }
        search_json = {
            "data": []
        }

        def side_effect(url, params=None):
            if "/es" in url:
                raise err_404
            elif "cards/search" in url:
                return _mock_resp(search_json)
            else:
                return _mock_resp(en_json)

        with patch("src.scryfall._throttled_get", side_effect=side_effect) as mock_get:
            card = fetch_card("ltr", "152", lang="es", fail_policy="alternative")

        assert card.front_url == "https://cards.scryfall.io/large/front/0/0/en.jpg"

    def test_download_card_images_custom_filename(self, tmp_path):
        card = DeckCard("Bolt", "ltr", "152", 1, "main")
        with (
            patch(
                "src.scryfall.fetch_card",
                return_value=ScryfallCard("https://example.com/img.jpg", None),
            ),
            patch("src.scryfall._throttled_get", return_value=_mock_resp(_NORMAL_CARD_JSON)),
        ):
            from src.scryfall import download_card_images
            front, back = download_card_images(card, tmp_path, lang="es", quality="png", quality_check=False)
            
        assert front.name == "ltr_152_es_png.jpg"


class TestScryfallQualityChecks:
    def test_check_image_quality_sharp_vs_blurry(self, tmp_path):
        from PIL import Image, ImageDraw
        from src.quality import check_image_quality
        
        # 1. Create a sharp image (high contrast edges) of size 800x1120 (meets resolution threshold)
        sharp_img = Image.new("RGB", (800, 1120), color="white")
        draw = ImageDraw.Draw(sharp_img)
        # Draw some high contrast lines/patterns
        for i in range(0, 800, 20):
            draw.line([(i, 0), (i, 1120)], fill="black", width=5)
            draw.line([(0, i), (800, i)], fill="black", width=5)
        
        sharp_path = tmp_path / "sharp.png"
        sharp_img.save(sharp_path)
        
        # 2. Create a blurry image by applying a strong blur to the same pattern
        from PIL import ImageFilter
        blurry_img = sharp_img.filter(ImageFilter.GaussianBlur(radius=10))
        blurry_path = tmp_path / "blurry.png"
        blurry_img.save(blurry_path)
        
        # 3. Create a low resolution image (e.g. 100x140)
        low_res_img = sharp_img.resize((100, 140))
        low_res_path = tmp_path / "low_res.png"
        low_res_img.save(low_res_path)
        
        # Run quality checks
        # Let's check with threshold 50.0
        ok_sharp, score_sharp, dims_sharp = check_image_quality(sharp_path, 50.0)
        ok_blurry, score_blurry, dims_blurry = check_image_quality(blurry_path, 50.0)
        ok_low_res, score_low_res, dims_low_res = check_image_quality(low_res_path, 50.0)
        
        assert dims_sharp == (800, 1120)
        assert dims_blurry == (800, 1120)
        assert dims_low_res == (100, 140)
        
        # Sharp image should pass resolution and focus score checks
        assert ok_sharp is True
        assert score_sharp > 50.0
        
        # Blurry image should fail focus check
        assert ok_blurry is False
        assert score_blurry < 10.0
        
        # Low res image should fail resolution check
        assert ok_low_res is False

    def test_download_card_images_fallback_flow(self, tmp_path):
        from src.scryfall import download_card_images
        card = DeckCard("Dummy Card", "ltr", "152", 1, "main")
        
        # Mock fetch_card to return different ScryfallCards based on requested language
        def mock_fetch_card(s_code, col_num, lang="en", quality="large", fail_policy="english"):
            if lang == "es":
                return ScryfallCard("https://example.com/orig_es.jpg", None, oracle_id="oracle-id-123")
            else:
                return ScryfallCard("https://example.com/orig_en.jpg", None, oracle_id="oracle-id-123")
        
        # Mock _throttled_get to return status 200 for mock downloads
        def mock_get(url, params=None):
            return _mock_resp({}, status=200)

        # Mock check image quality: Spanish fails, English passes!
        qualities_returned = []
        def mock_check_quality(image_path, threshold, bc_check=True):
            qualities_returned.append(image_path.name)
            if "orig_es" in image_path.name or "_es_" in image_path.name:
                return False, 10.0, (800, 1120)
            else:
                return True, 150.0, (800, 1120)

        with (
            patch("src.scryfall.fetch_card", side_effect=mock_fetch_card),
            patch("src.scryfall._throttled_get", side_effect=mock_get),
            patch("src.scryfall.check_image_quality", side_effect=mock_check_quality),
        ):
            # Run with quality check enabled
            front_path, back_path = download_card_images(
                card, tmp_path, lang="es", quality="large", quality_check=True, blur_threshold=100.0
            )
            
        # Should have downloaded English original fallback print and returned its path
        assert "en_large" in front_path.name
        # The quality check was run on Spanish first, then English
        assert "ltr_152_es_large.jpg" in qualities_returned[0]
        assert "ltr_152_en_large.jpg" in qualities_returned[1]
