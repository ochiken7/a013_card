"""Google Cloud Vision API によるOCR処理 + 画像前処理"""

import io
import base64
import time
import logging

import cv2
import numpy as np
import requests
from PIL import Image, ImageOps
from flask import current_app

logger = logging.getLogger(__name__)

VISION_API_URL = "https://vision.googleapis.com/v1/images:annotate"


def _pil_to_cv(pil_img):
    """PIL Image → OpenCV (BGR) numpy配列"""
    rgb = np.array(pil_img)
    if len(rgb.shape) == 2:
        return cv2.cvtColor(rgb, cv2.COLOR_GRAY2BGR)
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def _cv_to_pil(cv_img):
    """OpenCV (BGR) numpy配列 → PIL Image"""
    return Image.fromarray(cv2.cvtColor(cv_img, cv2.COLOR_BGR2RGB))


def _detect_card(cv_img):
    """名刺の外枠を検出し、透視変換で長方形に補正する。
    複数の検出手法を試し、最良の結果を使う。
    失敗時は元画像を返す。
    """
    img_h, img_w = cv_img.shape[:2]
    img_area = img_h * img_w

    # 1. グレースケール化
    gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY)

    # 複数の二値化手法でエッジマスクを生成し、それぞれで四角形を探す
    candidates = []

    # --- 手法A: 適応的閾値（明暗差がある背景に強い） ---
    blur_a = cv2.GaussianBlur(gray, (5, 5), 0)
    thresh_a = cv2.adaptiveThreshold(
        blur_a, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV, 11, 2
    )
    candidates.append(("adaptive", thresh_a))

    # --- 手法B: Otsu二値化（背景と名刺のコントラストが明確なケース） ---
    blur_b = cv2.GaussianBlur(gray, (5, 5), 0)
    _, thresh_b = cv2.threshold(blur_b, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    candidates.append(("otsu", thresh_b))

    # --- 手法C: Canny（複数パラメータ） ---
    blur_c = cv2.GaussianBlur(gray, (7, 7), 0)
    for low, high in [(20, 80), (40, 120), (60, 180)]:
        edges = cv2.Canny(blur_c, low, high)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        edges = cv2.dilate(edges, kernel, iterations=2)
        edges = cv2.erode(edges, kernel, iterations=1)
        candidates.append((f"canny_{low}_{high}", edges))

    # --- 手法D: HSV色空間で白い領域を検出（白い名刺に有効） ---
    hsv = cv2.cvtColor(cv_img, cv2.COLOR_BGR2HSV)
    # 白い領域: 低彩度 + 高明度
    white_mask = cv2.inRange(hsv, (0, 0, 180), (180, 40, 255))
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    white_mask = cv2.morphologyEx(white_mask, cv2.MORPH_CLOSE, kernel, iterations=3)
    white_mask = cv2.morphologyEx(white_mask, cv2.MORPH_OPEN, kernel, iterations=1)
    candidates.append(("white_detect", white_mask))

    # 各手法で四角形を探し、最も面積が大きい四角形を採用
    best_quad = None
    best_area = 0
    best_method = ""

    for method_name, binary_img in candidates:
        quad = _find_largest_quad(binary_img, img_area)
        if quad is not None:
            area = cv2.contourArea(quad)
            if area > best_area:
                best_area = area
                best_quad = quad
                best_method = method_name

    if best_quad is not None and best_area > img_area * 0.10:
        result = _perspective_transform(cv_img, best_quad)
        if result is not None:
            logger.info(f"名刺検出成功 (method={best_method}, area={best_area/img_area:.1%})")
            return result

    # 全手法で四角形が見つからない場合、余白除去にフォールバック
    logger.info("名刺四角形検出失敗、フォールバック余白除去を実行")
    return _fallback_crop(cv_img, gray)


def _find_largest_quad(binary_img, img_area):
    """二値画像から最大の四角形輪郭を見つける"""
    contours, _ = cv2.findContours(binary_img, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    contours = sorted(contours, key=cv2.contourArea, reverse=True)

    for cnt in contours[:10]:
        area = cv2.contourArea(cnt)
        if area < img_area * 0.10:
            break

        peri = cv2.arcLength(cnt, True)
        # 複数の近似精度で試す
        for eps in [0.02, 0.03, 0.04, 0.05]:
            approx = cv2.approxPolyDP(cnt, eps * peri, True)
            if len(approx) == 4:
                # 凸性チェック
                if cv2.isContourConvex(approx):
                    return approx.reshape(4, 2).astype(np.float32)

    return None


def _perspective_transform(cv_img, pts):
    """4点から透視変換して名刺サイズの長方形に補正"""
    rect = _order_points(pts)

    # 名刺の標準アスペクト比: 91mm x 55mm
    CARD_RATIO = 91.0 / 55.0

    width_a = np.linalg.norm(rect[0] - rect[1])
    width_b = np.linalg.norm(rect[3] - rect[2])
    detected_w = max(width_a, width_b)

    height_a = np.linalg.norm(rect[0] - rect[3])
    height_b = np.linalg.norm(rect[1] - rect[2])
    detected_h = max(height_a, height_b)

    if detected_w < 100 or detected_h < 100:
        return None

    # 横長・縦長どちらの向きかを判定
    if detected_w >= detected_h:
        out_w = int(detected_w)
        out_h = int(out_w / CARD_RATIO)
    else:
        out_h = int(detected_h)
        out_w = int(out_h / CARD_RATIO)

    dst = np.array([
        [0, 0], [out_w - 1, 0],
        [out_w - 1, out_h - 1], [0, out_h - 1]
    ], dtype=np.float32)

    matrix = cv2.getPerspectiveTransform(rect, dst)
    return cv2.warpPerspective(cv_img, matrix, (out_w, out_h))


def _fallback_crop(cv_img, gray):
    """四角形検出に失敗した場合の余白除去フォールバック"""
    # Otsu二値化で前景を検出
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7))
    thresh = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel, iterations=3)

    coords = cv2.findNonZero(thresh)
    if coords is None:
        return cv_img

    x, y, w, h = cv2.boundingRect(coords)
    img_h, img_w = cv_img.shape[:2]
    if w < img_w * 0.2 or h < img_h * 0.2:
        return cv_img

    margin = 10
    x = max(0, x - margin)
    y = max(0, y - margin)
    w = min(img_w - x, w + margin * 2)
    h = min(img_h - y, h + margin * 2)
    return cv_img[y:y+h, x:x+w]


def _order_points(pts):
    """4点を [左上, 右上, 右下, 左下] の順に並び替え"""
    rect = np.zeros((4, 2), dtype=np.float32)
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]   # 左上: x+y最小
    rect[2] = pts[np.argmax(s)]   # 右下: x+y最大
    d = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(d)]   # 右上: y-x最小
    rect[3] = pts[np.argmax(d)]   # 左下: y-x最大
    return rect


def _auto_rotate(cv_img):
    """名刺は横長が標準。縦長画像は90度回転する"""
    h, w = cv_img.shape[:2]
    if h > w * 1.2:
        # 縦長 → 90度時計回り回転
        return cv2.rotate(cv_img, cv2.ROTATE_90_CLOCKWISE)
    return cv_img


def preprocess_image(file_bytes):
    """Vision API精度向上のための画像前処理
    - EXIF回転補正（スマホ撮影対応）
    - 名刺検出（エッジ検出 → 四角形検出 → 透視変換 → 標準アスペクト比）
    - 自動回転（縦長→横長）
    - 長辺2048px制限
    - JPEG品質85で再圧縮
    """
    img = Image.open(io.BytesIO(file_bytes))
    img = ImageOps.exif_transpose(img)

    # RGBAの場合はRGBに変換
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")

    # OpenCVで画像処理
    cv_img = _pil_to_cv(img)
    cv_img = _detect_card(cv_img)
    cv_img = _auto_rotate(cv_img)
    img = _cv_to_pil(cv_img)

    # リサイズ
    max_size = 2048
    if max(img.size) > max_size:
        img.thumbnail((max_size, max_size), Image.LANCZOS)

    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=85)
    return buffer.getvalue()


def extract_text_from_image(image_bytes):
    """Google Cloud Vision API（APIキー方式）で文字を抽出（リトライ付き）"""
    api_key = current_app.config["GOOGLE_VISION_API_KEY"]

    # リクエストボディ
    body = {
        "requests": [
            {
                "image": {"content": base64.b64encode(image_bytes).decode("utf-8")},
                "features": [{"type": "TEXT_DETECTION"}],
            }
        ]
    }

    max_retries = 3
    for attempt in range(max_retries):
        try:
            resp = requests.post(
                VISION_API_URL,
                params={"key": api_key},
                json=body,
                timeout=30,
            )
            resp.raise_for_status()
            result = resp.json()

            # エラーチェック
            annotations = result.get("responses", [{}])[0]
            if "error" in annotations:
                raise Exception(f"Vision API error: {annotations['error']['message']}")

            text_annotations = annotations.get("textAnnotations", [])
            if not text_annotations:
                return ""

            return text_annotations[0]["description"]

        except Exception as e:
            if attempt == max_retries - 1:
                logger.error(f"Vision API failed after {max_retries} retries: {e}")
                raise
            delay = 1 * (2 ** attempt)
            logger.warning(f"Vision API retry {attempt + 1}: {e}")
            time.sleep(delay)
