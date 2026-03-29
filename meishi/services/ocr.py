"""Google Cloud Vision API によるOCR処理 + 画像前処理 + PDF対応"""

import io
import base64
import time
import logging

import cv2
import fitz  # PyMuPDF
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
    """名刺を検出し、切り出して補正する。
    1. グレースケール → ぼかし → Otsu二値化で名刺領域を検出
    2. 最大輪郭の最小外接矩形（minAreaRect）で四隅を取得
    3. 透視変換で長方形に補正
    4. 名刺の標準アスペクト比（91mm x 55mm）にリサイズ
    """
    img_h, img_w = cv_img.shape[:2]
    img_area = img_h * img_w

    gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (7, 7), 0)

    # Otsu二値化（名刺=明るい部分 を白にする）
    _, thresh = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # モルフォロジーで名刺内部の文字ノイズを埋め、外枠を整える
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15))
    thresh = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel, iterations=3)
    thresh = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel, iterations=1)

    # 最大の輪郭を探す
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        logger.info("名刺検出: 輪郭なし")
        return cv_img

    largest = max(contours, key=cv2.contourArea)
    area = cv2.contourArea(largest)

    # 画像の10%未満なら名刺ではない
    if area < img_area * 0.10:
        logger.info(f"名刺検出: 輪郭が小さすぎる ({area/img_area:.1%})")
        return cv_img

    # 画像の95%以上なら余白がほぼない→処理不要
    if area > img_area * 0.95:
        logger.info("名刺検出: 画像全体が名刺、補正スキップ")
        return cv_img

    # minAreaRectで回転を含む最小外接矩形の4隅を取得
    rect = cv2.minAreaRect(largest)
    box = cv2.boxPoints(rect)
    pts = _order_points(box.astype(np.float32))

    # 透視変換
    CARD_RATIO = 91.0 / 55.0
    w_a = np.linalg.norm(pts[0] - pts[1])
    w_b = np.linalg.norm(pts[3] - pts[2])
    h_a = np.linalg.norm(pts[0] - pts[3])
    h_b = np.linalg.norm(pts[1] - pts[2])
    det_w = max(w_a, w_b)
    det_h = max(h_a, h_b)

    if det_w < 100 or det_h < 100:
        return cv_img

    if det_w >= det_h:
        out_w = int(det_w)
        out_h = int(out_w / CARD_RATIO)
    else:
        out_h = int(det_h)
        out_w = int(out_h / CARD_RATIO)

    dst = np.array([
        [0, 0], [out_w - 1, 0],
        [out_w - 1, out_h - 1], [0, out_h - 1]
    ], dtype=np.float32)

    matrix = cv2.getPerspectiveTransform(pts, dst)
    result = cv2.warpPerspective(cv_img, matrix, (out_w, out_h))
    logger.info(f"名刺検出成功: {det_w:.0f}x{det_h:.0f} → {out_w}x{out_h} (面積{area/img_area:.0%})")
    return result


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


def pdf_to_images(pdf_bytes, max_pages=50):
    """PDFの各ページをJPEG画像バイト列のリストに変換する。
    max_pages: 変換する最大ページ数（デフォルト50）
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        if len(doc) == 0:
            raise ValueError("PDFにページがありません")
        page_count = min(len(doc), max_pages)
        images = []
        for page_num in range(page_count):
            page = doc[page_num]
            # 300dpi相当でレンダリング（名刺サイズなら十分な解像度）
            pix = page.get_pixmap(dpi=300)
            img_bytes = pix.tobytes("jpeg", jpg_quality=90)
            images.append(img_bytes)
        return images
    finally:
        doc.close()


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
