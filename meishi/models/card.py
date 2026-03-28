from meishi import db


class Card(db.Model):
    __tablename__ = "cards"

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id", ondelete="SET NULL"))
    registered_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)

    # 個人情報
    department = db.Column(db.String(255))
    position = db.Column(db.String(255))
    name_kanji = db.Column(db.String(255))
    name_kana = db.Column(db.String(255))
    name_romaji = db.Column(db.String(255))

    # 住所
    zip_code = db.Column(db.String(20))
    address = db.Column(db.String(500))
    building = db.Column(db.String(255))

    # Web・SNS
    website = db.Column(db.String(500))
    sns_info = db.Column(db.Text)

    # 裏面情報
    back_business_memo = db.Column(db.Text)
    back_branch_memo = db.Column(db.Text)

    # 管理
    visibility = db.Column(db.String(20), nullable=False, default="private")
    memo = db.Column(db.Text)
    created_at = db.Column(db.DateTime, nullable=False, server_default=db.func.now())
    updated_at = db.Column(db.DateTime, nullable=False, server_default=db.func.now(), onupdate=db.func.now())

    # 子テーブルリレーション
    phones = db.relationship("CardPhone", backref="card", cascade="all, delete-orphan",
                             order_by="CardPhone.sort_order")
    emails = db.relationship("CardEmail", backref="card", cascade="all, delete-orphan",
                             order_by="CardEmail.sort_order")
    qualifications = db.relationship("CardQualification", backref="card", cascade="all, delete-orphan",
                                     order_by="CardQualification.sort_order")
    images = db.relationship("CardImage", backref="card", cascade="all, delete-orphan")
    tags = db.relationship("Tag", secondary="card_tags", backref="cards")


class CardPhone(db.Model):
    __tablename__ = "card_phones"

    id = db.Column(db.Integer, primary_key=True)
    card_id = db.Column(db.Integer, db.ForeignKey("cards.id", ondelete="CASCADE"), nullable=False)
    phone_number = db.Column(db.String(50), nullable=False)
    phone_type = db.Column(db.String(20), nullable=False, default="main")
    sort_order = db.Column(db.SmallInteger, nullable=False, default=0)


class CardEmail(db.Model):
    __tablename__ = "card_emails"

    id = db.Column(db.Integer, primary_key=True)
    card_id = db.Column(db.Integer, db.ForeignKey("cards.id", ondelete="CASCADE"), nullable=False)
    email = db.Column(db.String(255), nullable=False)
    email_type = db.Column(db.String(20), nullable=False, default="company")
    sort_order = db.Column(db.SmallInteger, nullable=False, default=0)


class CardQualification(db.Model):
    __tablename__ = "card_qualifications"

    id = db.Column(db.Integer, primary_key=True)
    card_id = db.Column(db.Integer, db.ForeignKey("cards.id", ondelete="CASCADE"), nullable=False)
    qualification = db.Column(db.String(255), nullable=False)
    sort_order = db.Column(db.SmallInteger, nullable=False, default=0)


class CardImage(db.Model):
    __tablename__ = "card_images"

    id = db.Column(db.Integer, primary_key=True)
    card_id = db.Column(db.Integer, db.ForeignKey("cards.id", ondelete="CASCADE"), nullable=True)  # 確認画面前はNULL
    side = db.Column(db.String(10), nullable=False, default="front")
    r2_object_key = db.Column(db.String(500), nullable=False)
    original_filename = db.Column(db.String(255))
    ocr_raw_text = db.Column(db.Text)
    uploaded_at = db.Column(db.DateTime, nullable=False, server_default=db.func.now())
