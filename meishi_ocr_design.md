# 名刺管理アプリ OCRフロー設計書

作成日：2026年3月28日

---

## 処理フロー概要

```
画像アップロード
  → Cloudflare R2 保存
  → Google Cloud Vision API（TEXT_DETECTION）
  → OCR生テキストをDB保存（card_images.ocr_raw_text）
  → Claude API（構造化・項目分類）
  → 会社名マッチング
  → 確認画面（ユーザー修正）
  → DB保存
```

---

## Step 1: 画像アップロード＆R2保存

### 処理内容
- ユーザーがアップロードした画像をCloudflare R2に保存
- card_imagesレコードを作成（status: uploaded）

### R2オブジェクトキー命名規則
```
meishi/{user_id}/{card_id}/{side}_{timestamp}.{ext}
例: meishi/3/142/front_20260328_153045.jpg
```

### 画像前処理（サーバーサイド）
```python
from PIL import Image
import io

def preprocess_image(file_bytes: bytes) -> bytes:
    """Vision API精度向上のための前処理"""
    img = Image.open(io.BytesIO(file_bytes))
    
    # EXIF回転情報を適用（スマホ撮影時の向き補正）
    from PIL import ImageOps
    img = ImageOps.exif_transpose(img)
    
    # 長辺を2048pxに制限（Vision APIの推奨サイズ）
    max_size = 2048
    if max(img.size) > max_size:
        img.thumbnail((max_size, max_size), Image.LANCZOS)
    
    # JPEG品質85で再圧縮（R2ストレージ容量削減）
    buffer = io.BytesIO()
    img.save(buffer, format='JPEG', quality=85)
    return buffer.getvalue()
```

### 注意点
- スマホ撮影時のEXIF回転を必ず適用する（回転したまま送るとOCR精度が落ちる）
- ファイルサイズ上限: 10MB（フロントエンドでバリデーション）
- 対応形式: JPEG, PNG, WebP

---

## Step 2: Google Cloud Vision API

### API呼び出し
```python
from google.cloud import vision

def extract_text_from_image(image_bytes: bytes) -> str:
    """Google Cloud Vision APIで文字を抽出"""
    client = vision.ImageAnnotatorClient()
    
    image = vision.Image(content=image_bytes)
    
    # TEXT_DETECTION: 画像全体のテキストを抽出
    # DOCUMENT_TEXT_DETECTION ではなく TEXT_DETECTION を使用
    # → 名刺は密度が低いのでTEXT_DETECTIONで十分。コストも同じ。
    response = client.text_detection(image=image)
    
    if response.error.message:
        raise Exception(f"Vision API error: {response.error.message}")
    
    texts = response.text_annotations
    if not texts:
        return ""
    
    # texts[0].description が全テキスト（改行区切り）
    full_text = texts[0].description
    return full_text
```

### レスポンス例
```
株式会社 雄功電設
代表取締役 山田太郎
〒123-4567
東京都中央区日本橋1-2-3 ABCビル5F
TEL 03-1234-5678
FAX 03-1234-5679
携帯 090-9876-5432
E-mail yamada@yuko-d.co.jp
http://www.yuko-d.co.jp
一級電気工事施工管理技士
```

### 生テキスト保存
```python
# card_images テーブルに保存（再処理用）
card_image.ocr_raw_text = full_text
db.session.commit()
```

### コスト
- TEXT_DETECTION: $1.50 / 1,000リクエスト
- 月100枚 → $0.15/月

---

## Step 3: Claude API（構造化処理）

### 設計方針
- OCR生テキストをClaude APIに渡し、JSON形式で構造化データを返してもらう
- 表面と裏面を分けて処理（裏面があれば2回呼び出し、ではなく1回にまとめる）
- Haiku を使用（コスト重視、名刺の構造化にはHaikuで十分）

### プロンプト設計

```python
SYSTEM_PROMPT = """あなたは日本の名刺情報を構造化するアシスタントです。
OCRで読み取った名刺のテキストを、指定されたJSON形式に変換してください。

ルール:
1. 読み取れた情報のみをセットする。推測で埋めない。
2. 該当する情報がない項目はnullにする。
3. 電話番号の種別は内容から判断する（TEL→main, 直通→direct, 携帯→mobile, FAX→fax）
4. メールアドレスが個人ドメインか会社ドメインかを判断する
5. 資格・肩書きは複数ある場合はすべて配列に入れる
6. フリガナやローマ字は記載がある場合のみセットする
7. 郵便番号は「〒」を除去し、数字とハイフンのみにする
8. 電話番号のフォーマットはそのまま維持する（ハイフン等）
9. 裏面テキストが提供された場合、事業内容・拠点情報をフリーテキストとして保存する"""

USER_PROMPT_TEMPLATE = """以下のOCRテキストから名刺情報を構造化してください。

【表面テキスト】
{front_text}

{back_section}

以下のJSON形式で出力してください。JSONのみを出力し、他の説明は不要です。

{{
  "company_name_ja": "会社名（日本語）",
  "company_name_en": "会社名（英語/ローマ字）※なければnull",
  "department": "部署名 ※なければnull",
  "position": "役職 ※なければnull",
  "name_kanji": "氏名（漢字）",
  "name_kana": "氏名（フリガナ）※なければnull",
  "name_romaji": "氏名（ローマ字）※なければnull",
  "phones": [
    {{
      "number": "電話番号",
      "type": "main|direct|mobile|fax"
    }}
  ],
  "emails": [
    {{
      "address": "メールアドレス",
      "type": "company|personal"
    }}
  ],
  "qualifications": ["資格・肩書き1", "資格・肩書き2"],
  "zip_code": "郵便番号（数字とハイフンのみ）※なければnull",
  "address": "住所（建物名除く）※なければnull",
  "building": "建物名・部屋番号 ※なければnull",
  "website": "WebサイトURL ※なければnull",
  "sns_info": "SNSアカウント情報 ※なければnull",
  "back_business_memo": "裏面の事業内容 ※なければnull",
  "back_branch_memo": "裏面の拠点情報 ※なければnull"
}}"""
```

### 裏面テキストの差し込み
```python
def build_user_prompt(front_text: str, back_text: str = None) -> str:
    if back_text:
        back_section = f"【裏面テキスト】\n{back_text}"
    else:
        back_section = "※裏面なし"
    
    return USER_PROMPT_TEMPLATE.format(
        front_text=front_text,
        back_section=back_section
    )
```

### API呼び出し
```python
import anthropic
import json

def structure_card_data(front_text: str, back_text: str = None) -> dict:
    """Claude APIで名刺テキストを構造化"""
    client = anthropic.Anthropic()
    
    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": build_user_prompt(front_text, back_text)
            }
        ]
    )
    
    # レスポンスからJSONを抽出
    response_text = message.content[0].text
    
    # ```json ... ``` で囲まれている場合に対応
    if response_text.startswith("```"):
        response_text = response_text.strip("`").strip()
        if response_text.startswith("json"):
            response_text = response_text[4:].strip()
    
    return json.loads(response_text)
```

### レスポンス例
```json
{
  "company_name_ja": "株式会社 雄功電設",
  "company_name_en": null,
  "department": null,
  "position": "代表取締役",
  "name_kanji": "山田太郎",
  "name_kana": null,
  "name_romaji": null,
  "phones": [
    {"number": "03-1234-5678", "type": "main"},
    {"number": "03-1234-5679", "type": "fax"},
    {"number": "090-9876-5432", "type": "mobile"}
  ],
  "emails": [
    {"address": "yamada@yuko-d.co.jp", "type": "company"}
  ],
  "qualifications": ["一級電気工事施工管理技士"],
  "zip_code": "123-4567",
  "address": "東京都中央区日本橋1-2-3",
  "building": "ABCビル5F",
  "website": "http://www.yuko-d.co.jp",
  "sns_info": null,
  "back_business_memo": null,
  "back_branch_memo": null
}
```

### コスト見積もり（Haiku使用）
- 入力: システムプロンプト(~300トークン) + OCRテキスト(~200トークン) = ~500トークン
- 出力: JSON(~300トークン)
- Haiku料金: 入力 $0.80/MTok, 出力 $4.00/MTok
- 1枚あたり: 入力 $0.0004 + 出力 $0.0012 = **約$0.0016（約0.24円）**
- 月100枚: **約$0.16（約24円）**

---

## Step 4: 会社名マッチング

### 処理ロジック
```python
from sqlalchemy import func

def match_or_create_company(company_name_ja: str, company_name_en: str = None) -> int:
    """会社名でDBを検索し、一致すればそのIDを返す。なければ新規作成。"""
    if not company_name_ja:
        return None
    
    # 正規化: 全角スペース→半角、前後の空白除去、株式会社等の表記揺れ対応
    normalized = normalize_company_name(company_name_ja)
    
    # 完全一致検索（正規化済み名前で）
    existing = Company.query.filter(
        func.normalize_company_name(Company.name_ja) == normalized,
        Company.merged_into_id.is_(None)  # 統合済みは除外
    ).first()
    
    if existing:
        return existing.id
    
    # 新規作成
    company = Company(
        name_ja=company_name_ja,
        name_en=company_name_en
    )
    db.session.add(company)
    db.session.flush()  # IDを取得
    return company.id


def normalize_company_name(name: str) -> str:
    """会社名の表記揺れを正規化"""
    import re
    name = name.strip()
    name = name.replace("　", " ")  # 全角→半角スペース
    # （株）→ 株式会社 等の正規化
    name = re.sub(r'[（\(]株[）\)]', '株式会社', name)
    name = re.sub(r'[（\(]有[）\)]', '有限会社', name)
    name = re.sub(r'[（\(]合[）\)]', '合同会社', name)
    # 連続スペースを1つに
    name = re.sub(r'\s+', ' ', name)
    return name
```

---

## Step 5: 確認画面への受け渡し

### Claude APIのJSON → フォームデータ変換
```python
def structured_to_form_data(structured: dict) -> dict:
    """Claude APIの出力をフォーム表示用に変換"""
    return {
        "company_name_ja": structured.get("company_name_ja", ""),
        "company_name_en": structured.get("company_name_en", ""),
        "department": structured.get("department", ""),
        "position": structured.get("position", ""),
        "name_kanji": structured.get("name_kanji", ""),
        "name_kana": structured.get("name_kana", ""),
        "name_romaji": structured.get("name_romaji", ""),
        "phones": structured.get("phones", []),
        "emails": structured.get("emails", []),
        "qualifications": structured.get("qualifications", []),
        "zip_code": structured.get("zip_code", ""),
        "address": structured.get("address", ""),
        "building": structured.get("building", ""),
        "website": structured.get("website", ""),
        "sns_info": structured.get("sns_info", ""),
        "back_business_memo": structured.get("back_business_memo", ""),
        "back_branch_memo": structured.get("back_branch_memo", ""),
    }
```

### フォーム → DB保存
```python
def save_card_from_form(form_data: dict, user_id: int, 
                         card_image_ids: list, visibility: str) -> Card:
    """確認画面のフォームデータからカードを保存"""
    
    # 会社マッチング
    company_id = match_or_create_company(
        form_data["company_name_ja"],
        form_data.get("company_name_en")
    )
    
    # カード作成
    card = Card(
        company_id=company_id,
        registered_by=user_id,
        department=form_data.get("department") or None,
        position=form_data.get("position") or None,
        name_kanji=form_data["name_kanji"],
        name_kana=form_data.get("name_kana") or None,
        name_romaji=form_data.get("name_romaji") or None,
        zip_code=form_data.get("zip_code") or None,
        address=form_data.get("address") or None,
        building=form_data.get("building") or None,
        website=form_data.get("website") or None,
        sns_info=form_data.get("sns_info") or None,
        back_business_memo=form_data.get("back_business_memo") or None,
        back_branch_memo=form_data.get("back_branch_memo") or None,
        visibility=visibility,
    )
    db.session.add(card)
    db.session.flush()
    
    # 電話番号
    for i, phone in enumerate(form_data.get("phones", [])):
        if phone.get("number"):
            db.session.add(CardPhone(
                card_id=card.id,
                phone_number=phone["number"],
                phone_type=phone.get("type", "main"),
                sort_order=i
            ))
    
    # メール
    for i, email in enumerate(form_data.get("emails", [])):
        if email.get("address"):
            db.session.add(CardEmail(
                card_id=card.id,
                email=email["address"],
                email_type=email.get("type", "company"),
                sort_order=i
            ))
    
    # 資格
    for i, qual in enumerate(form_data.get("qualifications", [])):
        if qual:
            db.session.add(CardQualification(
                card_id=card.id,
                qualification=qual,
                sort_order=i
            ))
    
    # 画像をカードに紐付け
    for img_id in card_image_ids:
        img = CardImage.query.get(img_id)
        if img:
            img.card_id = card.id
    
    db.session.commit()
    return card
```

---

## バッチ処理フロー

### 概要
PCから複数画像を一括アップロードし、順次OCR処理する。

### 処理シーケンス
```
1. ユーザーが複数ファイルをアップロード
2. 各ファイルをR2に保存 + batch_items レコード作成
3. batch_job ステータスを 'processing' に更新
4. バックグラウンドで1枚ずつ処理:
   a. Vision API呼び出し
   b. Claude API構造化
   c. 仮カード作成（status=draft的な扱い）
   d. batch_item.status を 'completed' に更新
   e. batch_job.processed_count を +1
5. 全件完了 → batch_job.status を 'completed' に
6. ユーザーは各カードのOCR確認画面で修正・保存
```

### バックグラウンド処理
```python
import threading
import time

def process_batch(batch_id: int):
    """バッチジョブをバックグラウンドで処理"""
    with app.app_context():
        batch = BatchJob.query.get(batch_id)
        batch.status = 'processing'
        db.session.commit()
        
        items = BatchItem.query.filter_by(
            batch_id=batch_id, status='pending'
        ).all()
        
        for item in items:
            try:
                item.status = 'processing'
                db.session.commit()
                
                # R2から画像取得
                image_bytes = get_from_r2(item.r2_object_key)
                
                # Vision API
                ocr_text = extract_text_from_image(image_bytes)
                
                # Claude API
                structured = structure_card_data(ocr_text)
                
                # 仮カード作成（確認待ち）
                card = create_draft_card(structured, batch.created_by)
                item.card_id = card.id
                item.status = 'completed'
                
                # API費用削減のため1秒間隔
                time.sleep(1)
                
            except Exception as e:
                item.status = 'failed'
                item.error_message = str(e)
            
            batch.processed_count += 1
            db.session.commit()
        
        batch.status = 'completed'
        batch.completed_at = datetime.utcnow()
        db.session.commit()


# エンドポイント
@batch_bp.route('/batch/start', methods=['POST'])
def start_batch():
    batch = BatchJob.query.get(request.form['batch_id'])
    # バックグラウンドスレッドで実行
    thread = threading.Thread(target=process_batch, args=(batch.id,))
    thread.daemon = True
    thread.start()
    return jsonify({"status": "started"})
```

### 進捗確認API
```python
@batch_bp.route('/batch/<int:batch_id>/status')
def batch_status(batch_id):
    batch = BatchJob.query.get_or_404(batch_id)
    return jsonify({
        "status": batch.status,
        "total": batch.total_count,
        "processed": batch.processed_count,
        "items": [
            {
                "id": item.id,
                "filename": item.original_filename,
                "status": item.status,
                "card_id": item.card_id,
                "error": item.error_message
            }
            for item in batch.items
        ]
    })
```

---

## エラーハンドリング

### Vision APIエラー
| エラー | 対応 |
|---|---|
| 画像が読めない | ユーザーに「画像を再撮影してください」と表示 |
| テキストが検出されない | ユーザーに「文字が検出できませんでした。手動入力してください」→空のフォーム表示 |
| API制限 | 1秒待ってリトライ（最大3回） |
| 認証エラー | 管理者にメール通知、ユーザーには「一時的なエラー」表示 |

### Claude APIエラー
| エラー | 対応 |
|---|---|
| JSON解析失敗 | リトライ（最大2回）。それでも失敗→OCR生テキストを表示し手動入力 |
| レート制限 | 2秒待ってリトライ |
| API停止 | OCR生テキストを確認画面にプレーンテキストで表示、手動入力モードに切替 |

### リトライロジック
```python
import time

def call_with_retry(func, max_retries=3, initial_delay=1):
    """リトライ付きAPI呼び出し"""
    for attempt in range(max_retries):
        try:
            return func()
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            delay = initial_delay * (2 ** attempt)  # 指数バックオフ
            time.sleep(delay)
```

---

## 再処理機能

OCR生テキストをcard_imagesに保存しているので、以下のシナリオで再処理が可能:

1. **プロンプト改善時**: Claude APIのプロンプトを更新した後、既存の名刺に対して再構造化
2. **モデル変更時**: HaikuからSonnetへの変更テスト
3. **一括再処理**: 管理画面から選択した名刺をまとめて再構造化

```python
def reprocess_card(card_id: int) -> dict:
    """既存カードのOCRテキストで再構造化"""
    images = CardImage.query.filter_by(card_id=card_id).all()
    
    front_text = None
    back_text = None
    for img in images:
        if img.side == 'front' and img.ocr_raw_text:
            front_text = img.ocr_raw_text
        elif img.side == 'back' and img.ocr_raw_text:
            back_text = img.ocr_raw_text
    
    if not front_text:
        raise ValueError("OCRテキストがありません")
    
    return structure_card_data(front_text, back_text)
```

---

## コスト総まとめ（月100枚想定）

| 項目 | 単価 | 月額 |
|---|---|---|
| Google Vision API | $1.50/1,000枚 | $0.15 |
| Claude API (Haiku) | ~$0.0016/枚 | $0.16 |
| Cloudflare R2 ストレージ | $0.015/GB/月 | ~$0.01 |
| R2 操作 | $0.36/100万リクエスト | ~$0.01 |
| **合計** | | **約$0.33/月（約50円）** |

※ バッチ処理で大量に処理しても、1枚あたりのAPI費用は変わらない。

---

## 次のステップ

1. ~~DBテーブル設計~~ ✅
2. ~~画面設計~~ ✅
3. ~~OCRフロー設計~~ ✅
4. **Cloudflare R2接続設定** ← 次
5. Flaskアプリ実装
