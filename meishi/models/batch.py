from meishi import db


class BatchJob(db.Model):
    __tablename__ = "batch_jobs"

    id = db.Column(db.Integer, primary_key=True)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="pending")
    total_count = db.Column(db.Integer, nullable=False, default=0)
    processed_count = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, server_default=db.func.now())
    completed_at = db.Column(db.DateTime)

    items = db.relationship("BatchItem", backref="batch_job", cascade="all, delete-orphan")


class BatchItem(db.Model):
    __tablename__ = "batch_items"

    id = db.Column(db.Integer, primary_key=True)
    batch_id = db.Column(db.Integer, db.ForeignKey("batch_jobs.id", ondelete="CASCADE"), nullable=False)
    card_id = db.Column(db.Integer, db.ForeignKey("cards.id", ondelete="SET NULL"))
    r2_object_key = db.Column(db.String(500), nullable=False)
    original_filename = db.Column(db.String(255))
    status = db.Column(db.String(20), nullable=False, default="pending")
    error_message = db.Column(db.Text)
    created_at = db.Column(db.DateTime, nullable=False, server_default=db.func.now())
