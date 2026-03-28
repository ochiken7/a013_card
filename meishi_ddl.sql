-- ============================================================
-- 名刺管理アプリ DDL（PostgreSQL）
-- かなで行政書士法人
-- 作成日: 2026-03-28
-- ============================================================

-- ユーザーテーブル
-- 管理アカウント(is_admin=true)は「ユーザー登録ができる」権限のみ
CREATE TABLE users (
    id              SERIAL PRIMARY KEY,
    email           VARCHAR(255) NOT NULL UNIQUE,
    password_hash   VARCHAR(255) NOT NULL,
    display_name    VARCHAR(100) NOT NULL,
    is_admin        BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 会社テーブル（グルーピング用）
-- 同じ会社名の名刺を自動的にまとめる。手動統合・解除にも対応。
-- merged_into_id: 統合された場合、統合先の会社IDを指す（NULLなら有効な会社）
CREATE TABLE companies (
    id              SERIAL PRIMARY KEY,
    name_ja         VARCHAR(255) NOT NULL,      -- 会社名（日本語）
    name_en         VARCHAR(255),               -- 会社名（英語/ローマ字）
    merged_into_id  INTEGER REFERENCES companies(id) ON DELETE SET NULL,
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_companies_name_ja ON companies(name_ja);
CREATE INDEX idx_companies_merged ON companies(merged_into_id);

-- 名刺テーブル（メイン）
CREATE TABLE cards (
    id              SERIAL PRIMARY KEY,
    company_id      INTEGER REFERENCES companies(id) ON DELETE SET NULL,
    registered_by   INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,

    -- 個人情報
    department      VARCHAR(255),               -- 部署名
    position        VARCHAR(255),               -- 役職
    name_kanji      VARCHAR(255),               -- 氏名（漢字）
    name_kana       VARCHAR(255),               -- 氏名（フリガナ）
    name_romaji     VARCHAR(255),               -- 氏名（ローマ字）

    -- 住所
    zip_code        VARCHAR(20),                -- 郵便番号
    address         VARCHAR(500),               -- 住所（メイン）
    building        VARCHAR(255),               -- 建物名・部屋番号

    -- Web・SNS
    website         VARCHAR(500),               -- Webサイト
    sns_info        TEXT,                        -- LINE/Instagram等（テキスト）

    -- 裏面情報（検索対象外のフリーテキスト）
    back_business_memo  TEXT,                    -- 事業内容メモ
    back_branch_memo    TEXT,                    -- 複数拠点情報

    -- 管理
    visibility      VARCHAR(20) NOT NULL DEFAULT 'private',  -- 'private' or 'shared'
    memo            TEXT,                        -- 自由記述メモ
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_cards_company ON cards(company_id);
CREATE INDEX idx_cards_registered_by ON cards(registered_by);
CREATE INDEX idx_cards_name_kanji ON cards(name_kanji);
CREATE INDEX idx_cards_name_kana ON cards(name_kana);
CREATE INDEX idx_cards_visibility ON cards(visibility);

-- 電話番号テーブル（1名刺に複数）
-- phone_type: 'main'(代表), 'direct'(直通), 'mobile'(携帯), 'fax'
CREATE TABLE card_phones (
    id              SERIAL PRIMARY KEY,
    card_id         INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    phone_number    VARCHAR(50) NOT NULL,
    phone_type      VARCHAR(20) NOT NULL DEFAULT 'main',
    sort_order      SMALLINT NOT NULL DEFAULT 0
);

CREATE INDEX idx_card_phones_card ON card_phones(card_id);
CREATE INDEX idx_card_phones_number ON card_phones(phone_number);

-- メールアドレステーブル（1名刺に複数）
-- email_type: 'personal'(個人), 'company'(会社)
CREATE TABLE card_emails (
    id              SERIAL PRIMARY KEY,
    card_id         INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    email           VARCHAR(255) NOT NULL,
    email_type      VARCHAR(20) NOT NULL DEFAULT 'company',
    sort_order      SMALLINT NOT NULL DEFAULT 0
);

CREATE INDEX idx_card_emails_card ON card_emails(card_id);

-- 資格・肩書きテーブル（1名刺に複数）
CREATE TABLE card_qualifications (
    id              SERIAL PRIMARY KEY,
    card_id         INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    qualification   VARCHAR(255) NOT NULL,
    sort_order      SMALLINT NOT NULL DEFAULT 0
);

CREATE INDEX idx_card_qualifications_card ON card_qualifications(card_id);

-- 名刺画像テーブル
-- 実体はCloudflare R2に保存。DBにはキー情報のみ。
-- side: 'front'(表) or 'back'(裏)
CREATE TABLE card_images (
    id              SERIAL PRIMARY KEY,
    card_id         INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    side            VARCHAR(10) NOT NULL DEFAULT 'front',
    r2_object_key   VARCHAR(500) NOT NULL,      -- R2上のオブジェクトキー
    original_filename VARCHAR(255),              -- アップロード時のファイル名
    ocr_raw_text    TEXT,                        -- Google Vision APIの生テキスト（デバッグ・再処理用）
    uploaded_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_card_images_card ON card_images(card_id);

-- タグマスタ
CREATE TABLE tags (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(100) NOT NULL UNIQUE,
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 名刺×タグ 中間テーブル
CREATE TABLE card_tags (
    card_id         INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    tag_id          INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (card_id, tag_id)
);

CREATE INDEX idx_card_tags_tag ON card_tags(tag_id);

-- ============================================================
-- バッチ処理管理テーブル（複数枚まとめてアップロード用）
-- ============================================================
CREATE TABLE batch_jobs (
    id              SERIAL PRIMARY KEY,
    created_by      INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    status          VARCHAR(20) NOT NULL DEFAULT 'pending',  -- pending/processing/completed/failed
    total_count     INTEGER NOT NULL DEFAULT 0,
    processed_count INTEGER NOT NULL DEFAULT 0,
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at    TIMESTAMP
);

-- バッチ内の個別画像
CREATE TABLE batch_items (
    id              SERIAL PRIMARY KEY,
    batch_id        INTEGER NOT NULL REFERENCES batch_jobs(id) ON DELETE CASCADE,
    card_id         INTEGER REFERENCES cards(id) ON DELETE SET NULL,  -- 処理完了後にリンク
    r2_object_key   VARCHAR(500) NOT NULL,
    original_filename VARCHAR(255),
    status          VARCHAR(20) NOT NULL DEFAULT 'pending',  -- pending/processing/completed/failed
    error_message   TEXT,
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_batch_items_batch ON batch_items(batch_id);
