"""バッチ処理（複数名刺の一括アップロード＋OCR）"""

import logging
from flask import render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user

from meishi import db
from meishi.blueprints.batch import batch_bp
from meishi.models.batch import BatchJob, BatchItem
from meishi.models.card import Card, CardPhone, CardEmail, CardQualification, CardImage
from meishi.services.r2 import upload_image, generate_object_key, download_image
from meishi.services.ocr import preprocess_image, extract_text_from_image
from meishi.services.structurer import structure_card_data
from meishi.services.company_matcher import match_or_create_company

logger = logging.getLogger(__name__)


@batch_bp.route("/batch")
@login_required
def index():
    """バッチ一覧＋アップロードフォーム"""
    jobs = (
        BatchJob.query
        .filter_by(created_by=current_user.id)
        .order_by(BatchJob.created_at.desc())
        .all()
    )
    return render_template("batch/index.html", jobs=jobs)


@batch_bp.route("/batch", methods=["POST"])
@login_required
def upload():
    """複数画像をアップロードしてバッチジョブを作成"""
    files = request.files.getlist("images")

    # 画像ファイルのみ抽出
    valid_files = [f for f in files if f and f.filename]
    if not valid_files:
        flash("画像ファイルを選択してください。", "warning")
        return redirect(url_for("batch.index"))

    # バッチジョブ作成
    job = BatchJob(
        created_by=current_user.id,
        status="pending",
        total_count=len(valid_files),
        processed_count=0,
    )
    db.session.add(job)
    db.session.flush()

    # 各ファイルをR2にアップロード＋BatchItem作成
    for f in valid_files:
        try:
            raw_bytes = f.read()
            image_bytes = preprocess_image(raw_bytes)
            object_key = generate_object_key(current_user.id, "front", f.filename)
            upload_image(image_bytes, object_key)

            item = BatchItem(
                batch_id=job.id,
                r2_object_key=object_key,
                original_filename=f.filename,
                status="pending",
            )
            db.session.add(item)
        except Exception as e:
            logger.error(f"バッチアップロード失敗 ({f.filename}): {e}")
            # アップロード失敗のアイテムも記録する
            item = BatchItem(
                batch_id=job.id,
                r2_object_key="",
                original_filename=f.filename,
                status="failed",
                error_message=f"アップロード失敗: {e}",
            )
            db.session.add(item)

    db.session.commit()
    flash(f"{len(valid_files)} 枚の画像をアップロードしました。", "success")
    return redirect(url_for("batch.index"))


@batch_bp.route("/batch/<int:job_id>/process", methods=["POST"])
@login_required
def process(job_id):
    """バッチジョブのOCR処理を実行"""
    job = BatchJob.query.get_or_404(job_id)

    # 自分のジョブのみ処理可能
    if job.created_by != current_user.id:
        flash("このバッチジョブにアクセスする権限がありません。", "danger")
        return redirect(url_for("batch.index"))

    if job.status not in ("pending", "failed"):
        flash("このバッチジョブは既に処理済みです。", "warning")
        return redirect(url_for("batch.detail", job_id=job.id))

    job.status = "processing"
    job.processed_count = 0
    db.session.commit()

    # 各アイテムを処理
    pending_items = [item for item in job.items if item.status in ("pending",)]
    for item in pending_items:
        try:
            # R2から画像をダウンロード
            image_bytes = download_image(item.r2_object_key)

            # OCRでテキスト抽出
            text = extract_text_from_image(image_bytes)

            # CardImageを作成（OCR生テキストを保存）
            card_image = CardImage(
                side="front",
                r2_object_key=item.r2_object_key,
                original_filename=item.original_filename,
                ocr_raw_text=text,
            )
            db.session.add(card_image)
            db.session.flush()

            # Claude APIで構造化
            structured = structure_card_data(text)

            # 会社名マッチング
            company_name = structured.get("company_name_ja", "")
            company_kana = structured.get("company_name_kana")
            company_id = match_or_create_company(company_name, company_kana) if company_name else None

            # 名刺レコード作成
            card = Card(
                company_id=company_id,
                registered_by=current_user.id,
                department=structured.get("department"),
                position=structured.get("position"),
                name_kanji=structured.get("name_kanji"),
                name_kana=structured.get("name_kana"),
                name_romaji=structured.get("name_romaji"),
                zip_code=structured.get("zip_code"),
                address=structured.get("address"),
                building=structured.get("building"),
                website=structured.get("website"),
                sns_info=structured.get("sns_info"),
                visibility="shared",
            )
            db.session.add(card)
            db.session.flush()

            # 画像をカードに紐付け
            card_image.card_id = card.id

            # 電話番号
            for i, p in enumerate(structured.get("phones", [])):
                if p.get("number"):
                    db.session.add(CardPhone(
                        card_id=card.id,
                        phone_number=p["number"],
                        phone_type=p.get("type", "main"),
                        sort_order=i,
                    ))

            # メールアドレス
            for i, e in enumerate(structured.get("emails", [])):
                if e.get("address"):
                    db.session.add(CardEmail(
                        card_id=card.id,
                        email=e["address"],
                        email_type=e.get("type", "company"),
                        sort_order=i,
                    ))

            # 資格
            for i, q in enumerate(structured.get("qualifications", [])):
                if q:
                    db.session.add(CardQualification(
                        card_id=card.id,
                        qualification=q,
                        sort_order=i,
                    ))

            item.card_id = card.id
            item.status = "completed"
            job.processed_count += 1

        except Exception as e:
            item.status = "failed"
            item.error_message = str(e)
            logger.error(f"バッチアイテム {item.id} 処理失敗: {e}")

    # ジョブ全体のステータス更新
    if job.processed_count == job.total_count:
        job.status = "completed"
    elif job.processed_count > 0:
        job.status = "completed"  # 一部成功
    else:
        job.status = "failed"
    job.completed_at = db.func.now()
    db.session.commit()

    success = job.processed_count
    failed = job.total_count - job.processed_count
    if failed > 0:
        flash(f"処理完了: {success}枚成功、{failed}枚失敗", "warning")
    else:
        flash(f"全{success}枚の処理が完了しました。", "success")

    return redirect(url_for("batch.detail", job_id=job.id))


@batch_bp.route("/batch/<int:job_id>")
@login_required
def detail(job_id):
    """バッチジョブの詳細・処理結果を表示"""
    job = BatchJob.query.get_or_404(job_id)

    if job.created_by != current_user.id:
        flash("このバッチジョブにアクセスする権限がありません。", "danger")
        return redirect(url_for("batch.index"))

    return render_template("batch/index.html", jobs=[job], detail_job=job)
