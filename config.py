import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", override=True)


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key-change-in-production")
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", "postgresql://localhost/meishi_db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Cloudflare R2
    R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID")
    R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID")
    R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY")
    R2_BUCKET_NAME = os.getenv("R2_BUCKET_NAME", "kanade-meishi")
    R2_ENDPOINT_URL = os.getenv(
        "R2_ENDPOINT_URL",
        f"https://{os.getenv('R2_ACCOUNT_ID', '')}.r2.cloudflarestorage.com"
    )

    # Google Cloud Vision API（APIキー方式）
    GOOGLE_VISION_API_KEY = os.getenv("GOOGLE_VISION_API_KEY")

    # Claude API
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

    # a001顧客システムAPI
    A001_API_KEY = os.getenv("A001_API_KEY", "")

    # アップロード制限
    MAX_CONTENT_LENGTH = 10 * 1024 * 1024  # 10MB
