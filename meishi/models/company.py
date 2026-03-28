from meishi import db


class Company(db.Model):
    __tablename__ = "companies"

    id = db.Column(db.Integer, primary_key=True)
    name_ja = db.Column(db.String(255), nullable=False)
    name_kana = db.Column(db.String(255))
    merged_into_id = db.Column(db.Integer, db.ForeignKey("companies.id", ondelete="SET NULL"))
    created_at = db.Column(db.DateTime, nullable=False, server_default=db.func.now())
    updated_at = db.Column(db.DateTime, nullable=False, server_default=db.func.now(), onupdate=db.func.now())

    # 自己参照リレーション（統合先・統合元）
    merged_into = db.relationship("Company", remote_side="Company.id", backref="merged_from")
    cards = db.relationship("Card", backref="company")
