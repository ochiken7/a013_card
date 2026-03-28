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


def _auto_crop(cv_img):
    """余白（白背景）を自動除去して名刺部分を切り抜く"""
    gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY)
    # 白背景を除去するため、閾値で二値化（暗い部分を検出）
    _, thresh = cv2.threshold(gray, 230, 255, cv2.THRESH_BINARY_INV)

    # ノイズ除去
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    thresh = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel)

    # 非白色領域のバウンディングボックス
    coords = cv2.findNonZero(thresh)
    if coords is None:
        return cv_img

    x, y, w, h = cv2.boundingRect(coords)
    # 元画像の10%以下なら誤検出として無視
    img_h, img_w = cv_img.shape[:2]
    if w < img_w * 0.1 or h < img_h * 0.1:
        return cv_img

    # 少し余白を残す（5px）
    margin = 5
    x = max(0, x - margin)
    y = max(0, y - margin)
    w = min(img_w - x, w + margin * 2)
    h = min(img_h - y, h + margin * 2)

    return cv_img[y:y+h, x:x+w]


def _perspective_correct(cv_img):
    """台形補正: 最大の四角形輪郭を検出して射影変換する。失敗時は元画像を返す"""
    gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 50, 150)

    # 輪郭検出
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return cv_img

    # 面積が大きい順にソートし、四角形を探す
    contours = sorted(contours, key=cv2.contourArea, reverse=True)
    img_area = cv_img.shape[0] * cv_img.shape[1]

    for cnt in contours[:5]:
        area = cv2.contourArea(cnt)
        # 画像の20%未満の輪郭は無視
        if area < img_area * 0.2:
            break

        peri = cv2.arcLength(cnt, True)
        approx = cv2.approxPolyDP(cnt, 0.02 * peri, True)

        if len(approx) == 4:
            pts = approx.reshape(4, 2).astype(np.float32)
            # 4点を左上・右上・右下・左下に並び替え
            rect = _order_points(pts)

            # 変換先のサイズを計算
            width_a = np.linalg.norm(rect[0] - rect[1])
            width_b = np.linalg.norm(rect[3] - rect[2])
            max_width = int(max(width_a, width_b))

            height_a = np.linalg.norm(rect[0] - rect[3])
            height_b = np.linalg.norm(rect[1] - rect[2])
            max_height = int(max(height_a, height_b))

            if max_width < 100 or max_height < 100:
                continue

            dst = np.array([
                [0, 0], [max_width - 1, 0],
                [max_width - 1, max_height - 1], [0, max_height - 1]
            ], dtype=np.float32)

            matrix = cv2.getPerspectiveTransform(rect, dst)
            return cv2.warpPerspective(cv_img, matrix, (max_width, max_height))

    return cv_img


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


def preprocess_image(file_bytes, rotation=0):
    """Vision API精度向上のための画像前処理
    - EXIF回転補正（スマホ撮影対応）
    - 手動回転（rotation: 0, 90, 180, 270）
    - 自動クロップ（余白除去）
    - 台形補正（射影変換）
    - 自動回転（縦長→横長）
    - 長辺2048px制限
    - JPEG品質85で再圧縮
    """
    img = Image.open(io.BytesIO(file_bytes))
    img = ImageOps.exif_transpose(img)

    # 手動回転（ユーザー指定）
    if rotation:
        img = img.rotate(-rotation, expand=True)

    # RGBAの場合はRGBに変換
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")

    # OpenCVで画像処理
    cv_img = _pil_to_cv(img)
    cv_img = _auto_crop(cv_img)
    cv_img = _perspective_correct(cv_img)
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
