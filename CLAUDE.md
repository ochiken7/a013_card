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
name_en VARCHAR(255)             -- 会社名（英語）
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
画像アップロード
  → PIL前処理（EXIF回転補正 + 長辺2048px制限 + JPEG品質85）
  → Cloudflare R2に保存（boto3）
  → Google Cloud Vision API（TEXT_DETECTION）で全文字抽出
  → card_images.ocr_raw_text に生テキスト保存 ★重要：再処理用
  → Claude API (Haiku) でJSON構造化
  → 会社名マッチング（正規化して既存companiesと照合）
  → 確認画面でユーザーが修正
  → DB保存
```

### Claude API 構造化プロンプト（概要）
- systemプロンプト: 「日本の名刺情報を構造化するアシスタント。読み取れた情報のみセット、推測禁止、nullを使う」
- userプロンプト: 表面テキスト（＋裏面テキストがあれば追加）→ 指定JSON形式で出力
- 出力JSON項目: company_name_ja, company_name_en, department, position, name_kanji, name_kana, name_romaji, phones[], emails[], qualifications[], zip_code, address, building, website, sns_info, back_business_memo, back_branch_memo

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
│   │   ├── ocr.py         # Google Vision API呼び出し + 画像前処理
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
| SSH認証 | .pemファイル / sudoパスワードなし |
| 配置先 | /var/www/vpsk/a013/ |
| ドメイン | a013.vpsk.net |
| Nginx | サブドメイン→Gunicornプロキシ |
| ポート | 使用中のポートを避けて設定 |

### デプロイ手順
```bash
# ローカル
git add -A && git commit -m "メッセージ" && git push

# VPS
cd /var/www/vpsk/a013
git pull
pip install -r requirements.txt
flask db upgrade
sudo systemctl restart a013
```

### Nginx設定（参考）
```nginx
server {
    server_name a013.vpsk.net;
    location / {
        proxy_pass http://127.0.0.1:PORT;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        client_max_body_size 20M;
    }
}
```

### systemd設定（参考）
```ini
[Unit]
Description=a013 meishi app
After=network.target

[Service]
User=vpsuser
WorkingDirectory=/var/www/vpsk/a013
Environment="PATH=/var/www/vpsk/a013/venv/bin"
EnvironmentFile=/var/www/vpsk/a013/.env
ExecStart=/var/www/vpsk/a013/venv/bin/gunicorn wsgi:app --bind 127.0.0.1:PORT --workers 2
Restart=always

[Install]
WantedBy=multi-user.target
```

---

## 実装順序

### Phase 1: 基盤（まずここから）
1. プロジェクト初期化（requirements.txt, config.py, create_app）
2. DBモデル定義（SQLAlchemy）+ マイグレーション
3. 認証（Flask-Login: ログイン/ログアウト）
4. 基本レイアウト（base.html + Bootstrap 5 + レスポンシブ）

### Phase 2: コア機能
5. 名刺一覧（検索・ソート・フィルター・アイウエオ順）
6. R2ユーティリティ（services/r2.py）
7. 名刺登録 + 画像アップロード→R2保存
8. OCR処理（services/ocr.py + services/structurer.py）
9. OCR確認画面 + DB保存
10. 名刺詳細・編集

### Phase 3: 拡張機能
11. 会社グルーピング（統合・解除）
12. タグ機能
13. バッチ処理（PC向け一括OCR）
14. CSVインポート/エクスポート
15. ユーザー管理（管理者画面）

### Phase 4: 仕上げ
16. PWA設定（manifest.json + Service Worker）
17. Nginx + Gunicorn + systemd 設定
18. 本番デプロイ・動作確認

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
