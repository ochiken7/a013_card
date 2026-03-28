"""Google Cloud Vision API によるOCR処理 + 画像前処理"""

import io
import base64
import time
import logging

import requests
from PIL import Image, ImageOps
from flask import current_app

logger = logging.getLogger(__name__)

VISION_API_URL = "https://vision.googleapis.com/v1/images:annotate"


def preprocess_image(file_bytes):
    """Vision API精度向上のための画像前処理
    - EXIF回転補正（スマホ撮影対応）
    - 長辺2048px制限
    - JPEG品質85で再圧縮
    """
    img = Image.open(io.BytesIO(file_bytes))
    img = ImageOps.exif_transpose(img)

    max_size = 2048
    if max(img.size) > max_size:
        img.thumbnail((max_size, max_size), Image.LANCZOS)

    # RGBAの場合はRGBに変換（JPEG保存用）
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")

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
