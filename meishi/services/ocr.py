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
    """名刺を検出し、回転補正して切り出す。
    アスペクト比は変更せず、余白除去と傾き補正のみ行う。
    1. グレースケール → ぼかし → Otsu二値化で名刺領域を検出
    2. minAreaRectで傾き角度を取得 → 画像を回転
    3. 回転後の画像からバウンディングボックスで切り出し
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

    # minAreaRectで傾き角度を取得
    rect = cv2.minAreaRect(largest)
    angle = rect[2]

    # OpenCVのminAreaRectの角度を -45〜+45度の範囲に補正
    if angle < -45:
        angle += 90
    elif angle > 45:
        angle -= 90

    # 傾きが小さい場合（±2度以内）は回転しない
    if abs(angle) < 2.0:
        x, y, w, h = cv2.boundingRect(largest)
        if w < 100 or h < 100:
            return cv_img
        result = cv_img[y:y+h, x:x+w]
        logger.info(f"名刺検出成功: 切り出し {w}x{h} (傾きなし)")
        return result

    # 画像を回転して名刺を水平にする
    center = (img_w / 2, img_h / 2)
    rot_matrix = cv2.getRotationMatrix2D(center, angle, 1.0)

    # 回転後のサイズを計算（画像が切れないように拡張）
    cos_a = abs(rot_matrix[0, 0])
    sin_a = abs(rot_matrix[0, 1])
    new_w = int(img_h * sin_a + img_w * cos_a)
    new_h = int(img_h * cos_a + img_w * sin_a)
    rot_matrix[0, 2] += (new_w - img_w) / 2
    rot_matrix[1, 2] += (new_h - img_h) / 2

    rotated = cv2.warpAffine(cv_img, rot_matrix, (new_w, new_h),
                              borderValue=(255, 255, 255))

    # 回転後の画像で再度二値化して名刺領域を切り出し
    gray2 = cv2.cvtColor(rotated, cv2.COLOR_BGR2GRAY)
    blurred2 = cv2.GaussianBlur(gray2, (7, 7), 0)
    _, thresh2 = cv2.threshold(blurred2, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    thresh2 = cv2.morphologyEx(thresh2, cv2.MORPH_CLOSE, kernel, iterations=3)
    thresh2 = cv2.morphologyEx(thresh2, cv2.MORPH_OPEN, kernel, iterations=1)

    contours2, _ = cv2.findContours(thresh2, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours2:
        return cv_img

    largest2 = max(contours2, key=cv2.contourArea)
    x, y, w, h = cv2.boundingRect(largest2)
    if w < 100 or h < 100:
        return cv_img
    result = rotated[y:y+h, x:x+w]

    logger.info(f"名刺検出成功: 切り出し {w}x{h} (傾き{angle:.1f}°)")
    return result


def _auto_rotate(cv_img):
    """名刺は横長が標準。縦長画像は90度回転する"""
    h, w = cv_img.shape[:2]
    if h > w * 1.2:
        # 縦長 → 90度時計回り回転
        return cv2.rotate(cv_img, cv2.ROTATE_90_CLOCKWISE)
    return cv_img


def preprocess_image_light(file_bytes):
    """EXIF補正+リサイズのみの軽量前処理（元画像保存用）。
    OpenCVによる名刺検出・台形補正はスキップする。
    """
    img = Image.open(io.BytesIO(file_bytes))
    img = ImageOps.exif_transpose(img)

    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")

    max_size = 2048
    if max(img.size) > max_size:
        img.thumbnail((max_size, max_size), Image.LANCZOS)

    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=85)
    return buffer.getvalue()


def preprocess_image(file_bytes):
    """Vision API精度向上のための画像前処理
    - EXIF回転補正（スマホ撮影対応）
    - 名刺検出（傾き補正 + 余白切り出し、アスペクト比は保持）
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
