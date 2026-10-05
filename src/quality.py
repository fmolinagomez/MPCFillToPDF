import logging
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

_log = logging.getLogger(__name__)

# Standard card dimensions
CARD_W_IN = 2.5
BLEED_X_FRAC = 0.042


def check_image_quality(
    image_path: Path,
    blur_threshold: float,
    check_brightness_contrast: bool = True,
) -> tuple[bool, float, tuple[int, int]]:
    """Evaluate image using standardized quality criteria.

    Checks integrity (PIL), aspect ratio, resolution (DPI >= 200),
    brightness (>= 50), contrast (>= 30), and sharpness (Laplacian variance >= threshold).

    Returns (is_ok, focus_score, (width, height)).
    """
    # 1. Integrity check using PIL
    try:
        with Image.open(image_path) as img:
            img.load()
            w, h = img.size
    except Exception as e:
        _log.warning("Imagen corrupta en control de calidad %s: %s", image_path.name, e)
        return False, 0.0, (0, 0)

    # 2. Aspect Ratio check
    ratio = w / h
    if not (0.68 <= ratio <= 0.77):
        _log.warning("Proporción incorrecta en control de calidad %s: %.3f", image_path.name, ratio)
        return False, 0.0, (w, h)

    # 3. Resolution (DPI) check
    w_cropped = w * (1.0 - 2.0 * BLEED_X_FRAC)
    dpi_cropped = round(w_cropped / CARD_W_IN)
    if dpi_cropped < 200:
        _log.warning(
            "Resolución insuficiente en control de calidad %s: %dx%d (%d DPI)",
            image_path.name,
            w,
            h,
            dpi_cropped,
        )
        return False, 0.0, (w, h)

    # 4. Decoded image checks (OpenCV)
    img_cv = cv2.imread(str(image_path))
    if img_cv is None:
        _log.warning("Error OpenCV al decodificar %s", image_path.name)
        return False, 0.0, (w, h)

    gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)

    # Sharpness (Laplacian variance)
    focus_score = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    if focus_score < blur_threshold:
        _log.warning(
            "Imagen borrosa en control de calidad %s: %.1f < %.1f",
            image_path.name,
            focus_score,
            blur_threshold,
        )
        return False, focus_score, (w, h)

    if check_brightness_contrast:
        # Brightness
        mean_val = np.mean(gray)
        if mean_val < 50:
            _log.warning(
                "Imagen demasiado oscura en control de calidad %s: %.1f < 50",
                image_path.name,
                mean_val,
            )
            return False, focus_score, (w, h)

        # Contrast
        std_val = np.std(gray)
        if std_val < 30:
            _log.warning(
                "Contraste insuficiente en control de calidad %s: %.1f < 30",
                image_path.name,
                std_val,
            )
            return False, focus_score, (w, h)

    return True, focus_score, (w, h)


def analyze_single_image(file_path: Path, check_brightness_contrast: bool = True) -> dict:
    """Analyze a single image and calculate detailed quality metrics.

    Returns a dictionary of formatted results matching the requirements of image_quality_analyzer.py.
    """
    res = {
        "filepath": str(file_path),
        "filename": file_path.name,
        "integrity": "OK",
        "resolution": "N/A",
        "dpi_est": "N/A",
        "sharpness": "N/A",
        "brightness": "N/A",
        "contrast": "N/A",
        "aspect": "N/A",
        "is_ok": True,
    }

    try:
        # 1. Integridad
        try:
            with Image.open(file_path) as img:
                img.load()
                w, h = img.size
        except Exception as e:
            res["integrity"] = f"Corrupto: {e}"
            res["is_ok"] = False
            return res

        # 2. Resolución y DPI Estimado
        res["resolution"] = f"{w}x{h}"
        w_cropped = w * (1.0 - 2.0 * BLEED_X_FRAC)
        dpi_cropped = round(w_cropped / CARD_W_IN)

        if dpi_cropped >= 300:
            dpi_label = "Alta"
        elif dpi_cropped >= 200:
            dpi_label = "Media"
        else:
            dpi_label = "Baja"
            res["is_ok"] = False

        res["dpi_est"] = f"{dpi_cropped} ({dpi_label})"

        # 3. Proporción
        ratio = w / h
        if 0.68 <= ratio <= 0.77:
            res["aspect"] = f"{ratio:.3f} (Correcto)"
        else:
            res["aspect"] = f"{ratio:.3f} (Incorrecto)"
            res["is_ok"] = False

        # OpenCV checks
        img_cv = cv2.imread(str(file_path))
        if img_cv is None:
            res["integrity"] = "Error OpenCV: No se pudo decodificar"
            res["is_ok"] = False
            return res

        gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)

        # 4. Nitidez
        lap_var = cv2.Laplacian(gray, cv2.CV_64F).var()
        if lap_var >= 300:
            sharp_label = "Alta (Nítido)"
        elif lap_var >= 100:
            sharp_label = "Media (Aceptable)"
        else:
            sharp_label = "Baja (Borroso)"
            res["is_ok"] = False
        res["sharpness"] = f"{lap_var:.1f} ({sharp_label})"

        # 5. Brillo y Contraste
        mean_val = np.mean(gray)
        std_val = np.std(gray)

        if check_brightness_contrast:
            if mean_val < 50:
                bright_label = "Muy Oscuro"
                res["is_ok"] = False
            elif mean_val > 200:
                bright_label = "Muy Claro"
            else:
                bright_label = "Normal"
            res["brightness"] = f"{mean_val:.1f} ({bright_label})"

            if std_val < 30:
                contrast_label = "Bajo"
                res["is_ok"] = False
            else:
                contrast_label = "Normal"
            res["contrast"] = f"{std_val:.1f} ({contrast_label})"
        else:
            if mean_val < 50:
                bright_label = "Muy Oscuro (Ignorado)"
            elif mean_val > 200:
                bright_label = "Muy Claro (Ignorado)"
            else:
                bright_label = "Normal"
            res["brightness"] = f"{mean_val:.1f} ({bright_label})"

            if std_val < 30:
                contrast_label = "Bajo (Ignorado)"
            else:
                contrast_label = "Normal"
            res["contrast"] = f"{std_val:.1f} ({contrast_label})"

    except Exception as e:
        res["integrity"] = f"Error inesperado: {e}"
        res["is_ok"] = False

    return res
