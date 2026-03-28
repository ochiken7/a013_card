"""Cloudflare R2 操作ユーティリティ（boto3 S3互換）"""

import uuid
import mimetypes
from datetime import datetime

import boto3
from botocore.config import Config as BotoConfig
from flask import current_app


def get_r2_client():
    """R2クライアントを取得"""
    return boto3.client(
        "s3",
        endpoint_url=current_app.config["R2_ENDPOINT_URL"],
        aws_access_key_id=current_app.config["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=current_app.config["R2_SECRET_ACCESS_KEY"],
        config=BotoConfig(
            signature_version="s3v4",
            retries={"max_attempts": 3, "mode": "adaptive"},
        ),
        region_name="auto",
    )


def generate_object_key(user_id, side, original_filename):
    """R2オブジェクトキーを生成
    形式: meishi/{user_id}/{date}/{uuid}_{side}.{ext}
    """
    ext = original_filename.rsplit(".", 1)[-1].lower() if "." in original_filename else "jpg"
    date_str = datetime.utcnow().strftime("%Y%m%d")
    unique_id = uuid.uuid4().hex[:8]
    return f"meishi/{user_id}/{date_str}/{unique_id}_{side}.{ext}"


def upload_image(image_bytes, object_key, content_type=None):
    """画像をR2にアップロード"""
    client = get_r2_client()
    bucket = current_app.config["R2_BUCKET_NAME"]

    if not content_type:
        ext = object_key.rsplit(".", 1)[-1]
        content_type = mimetypes.types_map.get(f".{ext}", "application/octet-stream")

    client.put_object(
        Bucket=bucket,
        Key=object_key,
        Body=image_bytes,
        ContentType=content_type,
    )
    return object_key


def download_image(object_key):
    """R2から画像をダウンロード"""
    client = get_r2_client()
    bucket = current_app.config["R2_BUCKET_NAME"]
    response = client.get_object(Bucket=bucket, Key=object_key)
    return response["Body"].read()


def get_presigned_url(object_key, expires_in=3600):
    """署名付きURLを生成（有効期限デフォルト1時間）"""
    client = get_r2_client()
    bucket = current_app.config["R2_BUCKET_NAME"]
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": object_key},
        ExpiresIn=expires_in,
    )


def delete_image(object_key):
    """R2から画像を削除"""
    client = get_r2_client()
    bucket = current_app.config["R2_BUCKET_NAME"]
    client.delete_object(Bucket=bucket, Key=object_key)


def delete_images_bulk(object_keys):
    """R2から複数画像を一括削除"""
    if not object_keys:
        return
    client = get_r2_client()
    bucket = current_app.config["R2_BUCKET_NAME"]
    for i in range(0, len(object_keys), 1000):
        batch = object_keys[i : i + 1000]
        client.delete_objects(
            Bucket=bucket,
            Delete={"Objects": [{"Key": key} for key in batch]},
        )
