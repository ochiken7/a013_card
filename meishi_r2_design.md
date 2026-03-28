# 名刺管理アプリ Cloudflare R2 接続設計書

作成日：2026年3月28日

---

## R2 概要

Cloudflare R2はS3互換のオブジェクトストレージ。
boto3（AWS SDK）でそのまま操作できる。エグレス（データ取得）無料が最大の特徴。

---

## 1. R2バケット作成手順

### Cloudflareダッシュボードで実施

1. Cloudflareダッシュボード → R2 Object Storage
2. 「Create bucket」をクリック
3. バケット名: `kanade-meishi`
4. リージョン: Asia Pacific（APAC）を選択
5. 作成完了

### APIトークン発行

1. R2 → Manage R2 API Tokens
2. 「Create API token」をクリック
3. 権限: Object Read & Write
4. 対象バケット: `kanade-meishi` のみに限定
5. トークン作成後、以下の3つを控える:
   - **Account ID** （ダッシュボードURLから取得）
   - **Access Key ID**
   - **Secret Access Key**

---

## 2. VPS側の環境設定

### 環境変数（.envファイル）
```bash
# Cloudflare R2
R2_ACCOUNT_ID=your_account_id
R2_ACCESS_KEY_ID=your_access_key_id
R2_SECRET_ACCESS_KEY=your_secret_access_key
R2_BUCKET_NAME=kanade-meishi
R2_ENDPOINT_URL=https://{account_id}.r2.cloudflarestorage.com
```

### pip インストール
```bash
pip install boto3
```

### Flaskコンフィグ（config.py）
```python
import os

class Config:
    # ... 他の設定 ...
    
    # Cloudflare R2
    R2_ACCOUNT_ID = os.environ.get('R2_ACCOUNT_ID')
    R2_ACCESS_KEY_ID = os.environ.get('R2_ACCESS_KEY_ID')
    R2_SECRET_ACCESS_KEY = os.environ.get('R2_SECRET_ACCESS_KEY')
    R2_BUCKET_NAME = os.environ.get('R2_BUCKET_NAME', 'kanade-meishi')
    R2_ENDPOINT_URL = os.environ.get(
        'R2_ENDPOINT_URL',
        f"https://{os.environ.get('R2_ACCOUNT_ID')}.r2.cloudflarestorage.com"
    )
```

---

## 3. R2ユーティリティ（services/r2.py）

```python
"""
Cloudflare R2 操作ユーティリティ
boto3（S3互換）でR2を操作する。
"""

import boto3
from botocore.config import Config as BotoConfig
from flask import current_app
from datetime import datetime
import uuid
import mimetypes


def get_r2_client():
    """R2クライアントを取得（シングルトン的に使い回し可能）"""
    return boto3.client(
        's3',
        endpoint_url=current_app.config['R2_ENDPOINT_URL'],
        aws_access_key_id=current_app.config['R2_ACCESS_KEY_ID'],
        aws_secret_access_key=current_app.config['R2_SECRET_ACCESS_KEY'],
        config=BotoConfig(
            signature_version='s3v4',
            retries={'max_attempts': 3, 'mode': 'adaptive'}
        ),
        region_name='auto'
    )


def generate_object_key(user_id: int, side: str, original_filename: str) -> str:
    """R2オブジェクトキーを生成
    
    形式: meishi/{user_id}/{date}/{uuid}_{side}.{ext}
    例:   meishi/3/20260328/a1b2c3d4_front.jpg
    """
    ext = original_filename.rsplit('.', 1)[-1].lower() if '.' in original_filename else 'jpg'
    date_str = datetime.utcnow().strftime('%Y%m%d')
    unique_id = uuid.uuid4().hex[:8]
    return f"meishi/{user_id}/{date_str}/{unique_id}_{side}.{ext}"


def upload_image(image_bytes: bytes, object_key: str, content_type: str = None) -> str:
    """画像をR2にアップロード
    
    Args:
        image_bytes: 画像のバイナリデータ
        object_key: R2上のキー（generate_object_keyで生成）
        content_type: MIMEタイプ（Noneなら拡張子から推定）
    
    Returns:
        アップロード先のオブジェクトキー
    """
    client = get_r2_client()
    bucket = current_app.config['R2_BUCKET_NAME']
    
    if not content_type:
        ext = object_key.rsplit('.', 1)[-1]
        content_type = mimetypes.types_map.get(f'.{ext}', 'application/octet-stream')
    
    client.put_object(
        Bucket=bucket,
        Key=object_key,
        Body=image_bytes,
        ContentType=content_type
    )
    
    return object_key


def download_image(object_key: str) -> bytes:
    """R2から画像をダウンロード
    
    Args:
        object_key: R2上のキー
    
    Returns:
        画像のバイナリデータ
    """
    client = get_r2_client()
    bucket = current_app.config['R2_BUCKET_NAME']
    
    response = client.get_object(Bucket=bucket, Key=object_key)
    return response['Body'].read()


def get_presigned_url(object_key: str, expires_in: int = 3600) -> str:
    """署名付きURLを生成（画像表示用）
    
    Args:
        object_key: R2上のキー
        expires_in: URLの有効期限（秒）デフォルト1時間
    
    Returns:
        署名付きURL
    """
    client = get_r2_client()
    bucket = current_app.config['R2_BUCKET_NAME']
    
    url = client.generate_presigned_url(
        'get_object',
        Params={'Bucket': bucket, 'Key': object_key},
        ExpiresIn=expires_in
    )
    return url


def delete_image(object_key: str) -> None:
    """R2から画像を削除
    
    Args:
        object_key: R2上のキー
    """
    client = get_r2_client()
    bucket = current_app.config['R2_BUCKET_NAME']
    
    client.delete_object(Bucket=bucket, Key=object_key)


def delete_images_bulk(object_keys: list) -> None:
    """R2から複数画像を一括削除（最大1000件）
    
    Args:
        object_keys: R2キーのリスト
    """
    if not object_keys:
        return
    
    client = get_r2_client()
    bucket = current_app.config['R2_BUCKET_NAME']
    
    # S3のdelete_objectsは最大1000件
    for i in range(0, len(object_keys), 1000):
        batch = object_keys[i:i+1000]
        client.delete_objects(
            Bucket=bucket,
            Delete={
                'Objects': [{'Key': key} for key in batch]
            }
        )
```

---

## 4. 画像表示の仕組み

### 方式: 署名付きURL（Presigned URL）

名刺画像はプライベートバケットに保存し、表示時に有効期限付きURLを発行する。
バケットをパブリックにしない（セキュリティ上重要）。

### Flaskルート（画像プロキシ）
```python
@cards_bp.route('/cards/image/<int:image_id>')
@login_required
def card_image(image_id):
    """名刺画像の署名付きURLにリダイレクト"""
    image = CardImage.query.get_or_404(image_id)
    card = Card.query.get_or_404(image.card_id)
    
    # アクセス権チェック
    if card.visibility == 'private' and card.registered_by != current_user.id:
        abort(403)
    
    url = get_presigned_url(image.r2_object_key, expires_in=3600)
    return redirect(url)
```

### テンプレートでの使用
```html
<!-- 名刺画像の表示 -->
<img src="{{ url_for('cards.card_image', image_id=image.id) }}" 
     alt="名刺画像" 
     loading="lazy">
```

### 注意点
- 署名付きURLは有効期限あり（1時間に設定）
- ブラウザキャッシュが効くので、同じ画像の再リクエストは少ない
- PWAでオフライン表示したい場合はService Workerでキャッシュ

---

## 5. アップロードフロー（統合版）

### 単体登録のエンドポイント
```python
from services.r2 import upload_image, generate_object_key
from services.ocr import extract_text_from_image, preprocess_image
from services.structurer import structure_card_data

@cards_bp.route('/cards/new', methods=['POST'])
@login_required
def create_card():
    """名刺登録（画像アップロード → OCR → 構造化 → 確認画面）"""
    
    front_file = request.files.get('front_image')
    back_file = request.files.get('back_image')
    visibility = request.form.get('visibility', 'shared')
    tags = request.form.get('tags', '')
    
    if not front_file:
        flash('表面の画像を選択してください', 'error')
        return redirect(url_for('cards.new_card'))
    
    card_image_ids = []
    front_text = None
    back_text = None
    
    # === 表面処理 ===
    # 1. 前処理
    front_bytes = preprocess_image(front_file.read())
    
    # 2. R2にアップロード
    front_key = generate_object_key(
        current_user.id, 'front', front_file.filename
    )
    upload_image(front_bytes, front_key, front_file.content_type)
    
    # 3. Vision API
    front_text = extract_text_from_image(front_bytes)
    
    # 4. DBに画像レコード保存
    front_image = CardImage(
        side='front',
        r2_object_key=front_key,
        original_filename=front_file.filename,
        ocr_raw_text=front_text
    )
    db.session.add(front_image)
    db.session.flush()
    card_image_ids.append(front_image.id)
    
    # === 裏面処理（あれば）===
    if back_file:
        back_bytes = preprocess_image(back_file.read())
        back_key = generate_object_key(
            current_user.id, 'back', back_file.filename
        )
        upload_image(back_bytes, back_key, back_file.content_type)
        back_text = extract_text_from_image(back_bytes)
        
        back_image = CardImage(
            side='back',
            r2_object_key=back_key,
            original_filename=back_file.filename,
            ocr_raw_text=back_text
        )
        db.session.add(back_image)
        db.session.flush()
        card_image_ids.append(back_image.id)
    
    db.session.commit()
    
    # === Claude API 構造化 ===
    try:
        structured = structure_card_data(front_text, back_text)
    except Exception:
        # 構造化失敗時は空フォームで確認画面へ
        structured = {}
    
    # 確認画面へ（セッションに一時保存）
    from flask import session
    session['ocr_data'] = {
        'structured': structured,
        'card_image_ids': card_image_ids,
        'visibility': visibility,
        'tags': tags,
        'front_text': front_text,
        'back_text': back_text,
    }
    
    return redirect(url_for('cards.confirm_card'))
```

---

## 6. R2料金まとめ

| 項目 | 無料枠 | 超過料金 |
|---|---|---|
| ストレージ | 10 GB/月 | $0.015/GB/月 |
| クラスA操作（PUT等） | 100万回/月 | $4.50/100万回 |
| クラスB操作（GET等） | 1,000万回/月 | $0.36/100万回 |
| エグレス（データ転送） | **無制限無料** | $0 |

月100枚の名刺画像（1枚平均500KB）なら:
- ストレージ: 50MB/月 → 無料枠内
- 操作: 数百回/月 → 無料枠内
- **実質無料**

---

## 次のステップ

1. ~~DBテーブル設計~~ ✅
2. ~~画面設計~~ ✅
3. ~~OCRフロー設計~~ ✅
4. ~~Cloudflare R2接続設定~~ ✅
5. **Flaskアプリ実装** ← 次
