# 名刺管理アプリ（a013_card）

## 基本情報

| 項目 | 値 |
|------|-----|
| プロジェクト名 | 名刺管理アプリ（a013_card） |
| 概要 | かなで行政書士法人の社内名刺管理Webアプリ。スマホ撮影→OCR自動読取→DB保存 |
| ローカルディレクトリ | `D:\kanade_system\app\a013_card` |
| URL | https://a013.vpsk.net/ |
| VPS配置先 | /var/www/vpsk/a013/ |

## 開発ルール

- ユーザーはプログラム初心者。難しい用語は避け、日本語で説明する
- 日本語でコミット・コメント可（コード内変数名は英語）
- 変更前に何をするか簡潔に説明してから実行する

## 開発環境

| 項目 | 値 |
|------|-----|
| ローカルOS | Windows 11 |
| Python | 3.12.10 |
| エディタ | Claude Code（`D:\` ドライブ） |
| サーバーOS | Ubuntu 25.04 |
| Webサーバー | Nginx + Gunicorn + Flask |
| GitHub | ochiken7 |
| デプロイ | ローカル → GitHub push → VPS で git pull |
| SSH | vpsuser / .pemファイル認証 / sudoパスワードなし |

## 技術スタック

| 項目 | 技術 |
|------|------|
| バックエンド | Flask（Blueprint構成）/ Python 3.12 |
| DB | PostgreSQL |
| ORM | SQLAlchemy + Flask-Migrate |
| 画像保存 | Cloudflare R2（boto3でS3互換操作） |
| OCR | Google Cloud Vision API（TEXT_DETECTION） |
| 構造化 | Claude API（Haiku）でOCRテキスト→JSON変換 |
| PDF変換 | PyMuPDF（fitz）でPDF→画像変換 |
| 画像処理 | OpenCV（opencv-python-headless）で名刺検出・台形補正 |
| フロントエンド | Jinja2 + Bootstrap 5 |
| 認証 | Flask-Login |
| スマホ対応 | PWA（manifest.json） |

---

## DBテーブル設計（10テーブル）

### users
ユーザー管理。is_admin=trueは「ユーザー登録権限」のみの違い。
```
id SERIAL PK
email VARCHAR(255) UNIQUE NOT NULL
password_hash VARCHAR(255) NOT NULL
display_name VARCHAR(100) NOT NULL
is_admin BOOLEAN DEFAULT FALSE
created_at TIMESTAMP
updated_at TIMESTAMP
```

### companies
会社グルーピング用。merged_into_idで統合管理。
```
id SERIAL PK
name_ja VARCHAR(255) NOT NULL    -- 会社名（日本語）
name_kana VARCHAR(255)           -- 会社名（フリガナ）
merged_into_id INTEGER FK→companies(id)  -- 統合先（NULLなら有効）
created_at, updated_at TIMESTAMP
```

### cards（メインテーブル）
```
id SERIAL PK
company_id INTEGER FK→companies(id)
registered_by INTEGER FK→users(id) NOT NULL
department VARCHAR(255)          -- 部署名
position VARCHAR(255)            -- 役職
name_kanji VARCHAR(255)          -- 氏名（漢字）
name_kana VARCHAR(255)           -- フリガナ
name_romaji VARCHAR(255)         -- ローマ字
zip_code VARCHAR(20)
address VARCHAR(500)
building VARCHAR(255)            -- 建物名・部屋番号
website VARCHAR(500)
sns_info TEXT                    -- LINE/Instagram等
back_business_memo TEXT          -- 裏面：事業内容（検索対象外）
back_branch_memo TEXT            -- 裏面：拠点情報（検索対象外）
visibility VARCHAR(20) DEFAULT 'private'  -- 'private' or 'shared'
memo TEXT                        -- 自由記述メモ
created_at, updated_at TIMESTAMP
```

### card_phones（1名刺→N件）
```
id SERIAL PK
card_id INTEGER FK→cards(id) ON DELETE CASCADE
phone_number VARCHAR(50) NOT NULL
phone_type VARCHAR(20) DEFAULT 'main'  -- main/direct/mobile/fax
sort_order SMALLINT DEFAULT 0
```

### card_emails（1名刺→N件）
```
id SERIAL PK
card_id INTEGER FK→cards(id) ON DELETE CASCADE
email VARCHAR(255) NOT NULL
email_type VARCHAR(20) DEFAULT 'company'  -- company/personal
sort_order SMALLINT DEFAULT 0
```

### card_qualifications（1名刺→N件）
```
id SERIAL PK
card_id INTEGER FK→cards(id) ON DELETE CASCADE
qualification VARCHAR(255) NOT NULL
sort_order SMALLINT DEFAULT 0
```

### card_images
画像実体はR2。DBにはキーとOCR生テキストを保持（再処理用）。
```
id SERIAL PK
card_id INTEGER FK→cards(id) ON DELETE CASCADE
side VARCHAR(10) DEFAULT 'front'  -- front/back
r2_object_key VARCHAR(500) NOT NULL
original_filename VARCHAR(255)
ocr_raw_text TEXT                 -- Vision APIの生テキスト（重要：再処理用に必ず保存）
uploaded_at TIMESTAMP
```

### tags / card_tags
```
tags: id SERIAL PK, name VARCHAR(100) UNIQUE
card_tags: card_id FK, tag_id FK → 複合PK
```

### batch_jobs / batch_items
PC向け複数枚一括OCR処理用。
```
batch_jobs: id, created_by FK→users, status(pending/processing/completed/failed), total_count, processed_count, created_at, completed_at
batch_items: id, batch_id FK, card_id FK(処理完了後リンク), r2_object_key, original_filename, status, error_message, created_at
```

### 検索用インデックス
cards.name_kanji, cards.name_kana, card_phones.phone_number, companies.name_ja

---

## 画面一覧（11画面）

| # | 画面 | URL | 概要 |
|---|------|-----|------|
| 1 | ログイン | /login | メール+パスワード |
| 2 | 名刺一覧 | / | 検索・ソート・フィルター。アイウエオ順セクション |
| 3 | 名刺登録 | /cards/new | カメラ起動 or ファイル選択 |
| 4 | OCR確認 | /cards/confirm | OCR結果の確認・修正フォーム |
| 5 | 名刺詳細 | /cards/<id> | 全情報表示+画像プレビュー |
| 6 | 名刺編集 | /cards/<id>/edit | 全項目編集 |
| 7 | 会社一覧 | /companies | グルーピング管理・統合・解除 |
| 8 | バッチ | /batch | 複数枚一括アップ+OCR（PC向け） |
| 9 | CSV | /csv | インポート/エクスポート |
| 10 | ユーザー管理 | /admin/users | 管理者のみ |
| 11 | 設定 | /settings | パスワード変更等 |

### レスポンシブ方針
- スマホ（<768px）: 1カラム、ハンバーガーメニュー、FABで新規登録
- PC（≥768px）: サイドバー + メインエリア2カラム

---

## OCRパイプライン（方式A: Vision + Claude 2段階）

```
画像/PDFアップロード
  → [クライアント側] 画像はCanvas APIで長辺2048px・JPEG85%に縮小してから送信
  → [クライアント側] PDFはそのまま送信（サーバー側で変換）
  → [サーバー側] PDFの場合: PyMuPDF(fitz)で300dpi画像に変換（1ページ目=表面、2ページ目=裏面）
  → OpenCV前処理（名刺検出→台形補正→自動回転）+ PIL（EXIF補正+リサイズ+JPEG変換）
  → Cloudflare R2に保存（boto3）
  → Google Cloud Vision API（TEXT_DETECTION）で全文字抽出
  → card_images.ocr_raw_text に生テキスト保存 ★重要：再処理用
  → Claude API (Haiku) でJSON構造化
  → 会社名マッチング（正規化して既存companiesと照合）
  → 確認画面でユーザーが修正（手動回転可能）
  → DB保存
```

### Claude API 構造化プロンプト（概要）
- systemプロンプト: 「日本の名刺情報を構造化するアシスタント。読み取れた情報のみセット、推測禁止、nullを使う」
- userプロンプト: 表面テキスト（＋裏面テキストがあれば追加）→ 指定JSON形式で出力
- 出力JSON項目: company_name_ja, company_name_kana, department, position, name_kanji, name_kana, name_romaji, phones[], emails[], qualifications[], zip_code, address, building, website, sns_info, back_business_memo, back_branch_memo
- name_kana: 漢字から推測して必ず生成する設定
- company_name_kana: 法人格除外のカタカナ読み

### 会社名正規化
- 全角スペース→半角、（株）→株式会社、連続スペース→1つ

### エラー時
- Vision API失敗 → リトライ3回 → 「再撮影してください」表示
- Claude API失敗 → リトライ2回 → OCR生テキスト表示＋手動入力モード
- JSON解析失敗 → リトライ → 空フォーム

---

## Cloudflare R2

### バケット
- バケット名: `kanade-meishi`
- リージョン: APAC

### オブジェクトキー命名
```
meishi/{user_id}/{date}/{uuid8}_{side}.{ext}
例: meishi/3/20260328/a1b2c3d4_front.jpg
```

### 画像表示方式
署名付きURL（Presigned URL）でリダイレクト。有効期限1時間。バケットはプライベート。

### 環境変数
```
R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET_NAME=kanade-meishi
R2_ENDPOINT_URL=https://{account_id}.r2.cloudflarestorage.com
```

---

## ディレクトリ構成

```
a013_card/
├── CLAUDE.md              ← このファイル
├── requirements.txt
├── run.py                 # ローカル起動用
├── wsgi.py                # Gunicorn用エントリーポイント
├── config.py              # 設定（DB, R2, API keys）
├── .env                   # 環境変数（Git管理外）
├── meishi/
│   ├── __init__.py        # Flaskアプリファクトリ create_app()
│   ├── models/
│   │   ├── __init__.py
│   │   ├── user.py
│   │   ├── card.py        # Card + CardPhone + CardEmail + CardQualification + CardImage
│   │   ├── company.py
│   │   ├── tag.py         # Tag + CardTag
│   │   └── batch.py       # BatchJob + BatchItem
│   ├── blueprints/
│   │   ├── auth/          # ログイン・ログアウト
│   │   │   ├── __init__.py
│   │   │   └── routes.py
│   │   ├── cards/         # 名刺CRUD + OCR + 確認
│   │   │   ├── __init__.py
│   │   │   └── routes.py
│   │   ├── companies/     # 会社管理・統合
│   │   │   ├── __init__.py
│   │   │   └── routes.py
│   │   ├── batch/         # バッチ処理
│   │   │   ├── __init__.py
│   │   │   └── routes.py
│   │   ├── csv_io/        # CSV入出力
│   │   │   ├── __init__.py
│   │   │   └── routes.py
│   │   └── admin/         # ユーザー管理（管理者のみ）
│   │       ├── __init__.py
│   │       └── routes.py
│   ├── services/
│   │   ├── ocr.py         # Vision API + OpenCV前処理 + PDF変換(PyMuPDF)
│   │   ├── structurer.py  # Claude API構造化（プロンプト定義含む）
│   │   ├── r2.py          # Cloudflare R2操作（upload/download/presigned/delete）
│   │   └── company_matcher.py  # 会社名正規化・マッチング
│   ├── templates/
│   │   ├── base.html      # 共通レイアウト（サイドバー+メイン）
│   │   ├── auth/
│   │   ├── cards/
│   │   ├── companies/
│   │   ├── batch/
│   │   ├── csv/
│   │   └── admin/
│   ├── static/
│   │   ├── css/
│   │   ├── js/
│   │   ├── manifest.json  # PWA用
│   │   └── icons/
│   └── utils/
│       └── helpers.py
└── migrations/            # Flask-Migrate
```

---

## VPS デプロイ情報

| 項目 | 値 |
|------|-----|
| SSHユーザー | vpsuser |
| SSH鍵 | D:\VPS\ssh-knd.pem |
| 配置先 | /var/www/vpsk/a013/ |
| ドメイン | a013.vpsk.net |
| Gunicornポート | 8013 |
| systemdサービス | a013.service |
| DB | PostgreSQL 17（DB名: meishi、Unixソケットpeer認証 `postgresql:///meishi`） |
| SSL | Let's Encrypt（自動更新あり） |
| GitHub | https://github.com/ochiken7/a013_card (public) |

### デプロイ手順
```bash
# ローカル
git add -A && git commit -m "メッセージ" && git push

# VPS（SSHで接続して実行）
cd /var/www/vpsk/a013
git pull
source venv/bin/activate
pip install -r requirements.txt   # 依存関係変更時のみ
flask db upgrade                   # マイグレーション時のみ
sudo systemctl restart a013
```

### SSH接続
```bash
ssh -i "D:\VPS\ssh-knd.pem" vpsuser@a013.vpsk.net
```

---

## 実装状況（2026-03-29 社内リリース済み）

### Phase 1〜4: 基盤〜デプロイ ✅
### カスタマイズ第1弾 ✅（2026-03-29）
- companies.name_en → name_kana（フリガナ化）
- OCRプロンプト更新（company_name_kana + name_kana必須生成）
- OpenCV画像処理（名刺検出・台形補正・自動回転）
- 確認画面での手動回転UI
- クライアント側画像圧縮（Canvas API、Request Entity Too Large対策）
- カメラ/写真選択ボタン分離
- FABセンタリング修正
- 一覧: 重複バッジ、五十音/アルファベットインデックスボタン
- 一覧: 会社名・肩書の行分離（text-truncate対応）

### カスタマイズ第2弾 ✅（2026-03-29）
- PDF名刺データ対応（名刺登録+バッチ処理）
- 名刺0件の会社を自動削除（delete_card / update_card時）

### 今後のカスタマイズ候補
- PWAアイコン画像（icon-192.png, icon-512.png）の作成
- 名刺一覧でのタグフィルター機能
- バッチ処理の非同期化（現在は同期で処理時間がかかる）
- 検索インデックスの最適化（PostgreSQL側）
- settings Blueprint に追加機能

### 画像処理の現状と課題
- OpenCVによる名刺検出（Otsu二値化+minAreaRect+透視変換）は実装済みだが、実際の写真での精度はユーザーから「うまくいっていない」と報告あり
- 名刺の背景が白い場合など、コントラスト不足で検出が失敗するケースがある
- 改善が必要な場合は_detect_card()を見直すこと

---

## コスト見積もり（月100枚）

| 項目 | 月額 |
|------|------|
| Google Vision API | $0.15 |
| Claude API (Haiku) | $0.16 |
| Cloudflare R2 | 無料枠内 |
| **合計** | **約$0.33（約50円）** |

---

## 補足：既存かなでシステムとの関係

- かなでシステムは vpsk.net のサブドメインでFlask Blueprint構成の複数アプリを運用中
- a013はその1つとして独立したFlaskアプリとして構築
- 他アプリとDBは別（PostgreSQLの別データベース）
