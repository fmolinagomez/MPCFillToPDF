
import pytest
from PIL import Image, ImageDraw, ImageFilter

from src.quality import analyze_single_image, check_image_quality


@pytest.fixture
def test_images(tmp_path):
    # 1. Create a sharp image of size 800x1120 (ratio ~0.714, DPI ~320 with bleed)
    sharp_img = Image.new("RGB", (800, 1120), color="white")
    draw = ImageDraw.Draw(sharp_img)
    for i in range(0, 800, 20):
        draw.line([(i, 0), (i, 1120)], fill="black", width=5)
        draw.line([(0, i), (800, i)], fill="black", width=5)
    sharp_path = tmp_path / "sharp.png"
    sharp_img.save(sharp_path)

    # 2. Create a blurry image
    blurry_img = sharp_img.filter(ImageFilter.GaussianBlur(radius=10))
    blurry_path = tmp_path / "blurry.png"
    blurry_img.save(blurry_path)

    # 3. Create a low resolution image
    low_res_img = sharp_img.resize((100, 140))
    low_res_path = tmp_path / "low_res.png"
    low_res_img.save(low_res_path)

    # 4. Create a dark image (mean brightness < 50)
    dark_img = Image.new("RGB", (800, 1120), color="black")
    dark_path = tmp_path / "dark.png"
    dark_img.save(dark_path)

    # 5. Create a low contrast image (std dev < 30)
    low_contrast_img = Image.new("RGB", (800, 1120), color="#808080")
    low_contrast_path = tmp_path / "low_contrast.png"
    low_contrast_img.save(low_contrast_path)

    return {
        "sharp": sharp_path,
        "blurry": blurry_path,
        "low_res": low_res_path,
        "dark": dark_path,
        "low_contrast": low_contrast_path,
    }


class TestQualityCommon:
    def test_sharp_image_passes(self, test_images):
        ok, score, dims = check_image_quality(test_images["sharp"], blur_threshold=50.0)
        assert ok is True
        assert score > 50.0
        assert dims == (800, 1120)

    def test_blurry_image_fails_sharpness(self, test_images):
        ok, score, dims = check_image_quality(test_images["blurry"], blur_threshold=50.0)
        assert ok is False
        assert score < 10.0

    def test_low_res_image_fails_resolution(self, test_images):
        ok, score, dims = check_image_quality(test_images["low_res"], blur_threshold=50.0)
        assert ok is False

    def test_dark_image_fails_brightness_when_enabled(self, test_images):
        # With brightness/contrast check enabled -> fail
        ok, score, dims = check_image_quality(test_images["dark"], blur_threshold=50.0, check_brightness_contrast=True)
        assert ok is False

        # With brightness/contrast check disabled -> pass (since sharpness of all-black is 0.0, set threshold=0.0)
        ok_ign, score_ign, dims_ign = check_image_quality(test_images["dark"], blur_threshold=0.0, check_brightness_contrast=False)
        assert ok_ign is True

    def test_low_contrast_image_fails_contrast_when_enabled(self, test_images):
        # With brightness/contrast check enabled -> fail
        ok, score, dims = check_image_quality(test_images["low_contrast"], blur_threshold=50.0, check_brightness_contrast=True)
        assert ok is False

        # With brightness/contrast check disabled -> pass (set threshold=0.0)
        ok_ign, score_ign, dims_ign = check_image_quality(test_images["low_contrast"], blur_threshold=0.0, check_brightness_contrast=False)
        assert ok_ign is True

    def test_analyze_single_image_output_format(self, test_images):
        res = analyze_single_image(test_images["sharp"], check_brightness_contrast=True)
        assert res["is_ok"] is True
        assert "Media" in res["dpi_est"]
        assert "Nítido" in res["sharpness"]
        assert res["integrity"] == "OK"

        res_dark = analyze_single_image(test_images["dark"], check_brightness_contrast=True)
        assert res_dark["is_ok"] is False
        assert "Muy Oscuro" in res_dark["brightness"]

        res_dark_disabled = analyze_single_image(test_images["dark"], check_brightness_contrast=False)
        # It still flags dark, but with "(Ignorado)" and doesn't fail is_ok (if other checks pass)
        assert "Muy Oscuro (Ignorado)" in res_dark_disabled["brightness"]
