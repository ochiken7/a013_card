"""名刺CRUD + OCR + 画像表示ルート"""

import io
import logging
from PIL import Image as PILImage
from flask import (
    render_template, redirect, url_for, flash, request,
    session, abort, jsonify,
)
from flask_login import login_required, current_user
from collections import Counter
from sqlalchemy import or_

from meishi import db
from meishi.blueprints.cards import cards_bp
from meishi.models.card import Card, CardPhone, CardEmail, CardQualification, CardImage
from meishi.models.company import Company
from meishi.models.tag import Tag, CardTag
from meishi.services.r2 import (
    upload_image, download_image, generate_object_key, get_presigned_url, delete_image,
)
from meishi.services.ocr import (
    preprocess_image, preprocess_image_light, extract_text_from_image, pdf_to_images,
)
from meishi.services.structurer import structure_card_data, structured_to_form_data
from meishi.services.company_matcher import match_or_create_company

logger = logging.getLogger(__name__)


# ========== 名刺一覧 ==========

@cards_bp.route("/")
@login_required
def index():
    """名刺一覧（検索・フィルター対応）"""
    query = request.args.get("q", "").strip()
    filter_type = request.args.get("filter", "all")  # all / shared / mine
    tag_filter = request.args.get("tag", "").strip()

    cards_query = Card.query.filter(Card.is_archived == False)  # noqa: E712

    # フィルター
    if filter_type == "mine":
        cards_query = cards_query.filter(Card.registered_by == current_user.id)
    elif filter_type == "shared":
        cards_query = cards_query.filter(Card.visibility == "shared")
    elif filter_type == "recent":
        # 登録順: 全カード（自分+共有）を新しい順
        cards_query = cards_query.filter(
            or_(
                Card.registered_by == current_user.id,
                Card.visibility == "shared",
            )
        )
    else:
        # 全て: 自分のカード + 共有カード
        cards_query = cards_query.filter(
            or_(
                Card.registered_by == current_user.id,
                Card.visibility == "shared",
            )
        )

    # 検索
    if query:
        search = f"%{query}%"
        cards_query = cards_query.filter(
            or_(
                Card.name_kanji.ilike(search),
                Card.name_kana.ilike(search),
                Card.company.has(Company.name_ja.ilike(search)),
                Card.phones.any(CardPhone.phone_number.ilike(search)),
            )
        )

    # タグフィルター
    if tag_filter:
        cards_query = cards_query.filter(Card.tags.any(Tag.name == tag_filter))

    # ソート
    if filter_type == "recent":
        cards_query = cards_query.order_by(Card.created_at.desc())
    else:
        cards_query = cards_query.order_by(
            Card.name_kana.asc().nullslast(),
            Card.name_kanji.asc().nullslast(),
        )

    cards = cards_query.all()

    # 重複検出（同一 company_id + name_kanji）
    dup_counter = Counter(
        (card.company_id, card.name_kanji)
        for card in cards
        if card.company_id and card.name_kanji
    )
    duplicate_ids = set()
    for card in cards:
        key = (card.company_id, card.name_kanji)
        if card.company_id and card.name_kanji and dup_counter[key] > 1:
            duplicate_ids.add(card.id)

    # タグ一覧（フィルタードロップダウン用）
    all_tags = Tag.query.order_by(Tag.name).all()

    # 登録順の場合はセクション分けしない
    if filter_type == "recent":
        return render_template(
            "cards/index.html",
            sections={},
            all_sections=[],
            flat_cards=cards,
            duplicate_ids=duplicate_ids,
            query=query,
            filter_type=filter_type,
            tag_filter=tag_filter,
            all_tags=all_tags,
            total_count=len(cards),
        )

    # アイウエオ順セクション分け
    sections = {}
    for card in cards:
        if card.name_kana:
            first_char = card.name_kana[0]
            section = _get_kana_section(first_char)
        else:
            section = "その他"
        sections.setdefault(section, []).append(card)

    # インデックスボタン用: 存在するセクション一覧
    all_sections = list(sections.keys())

    # 各カードのフリガナ先頭文字を集める（ボタンのハイライト用）
    all_kana_chars = set()
    for card in cards:
        if card.name_kana:
            all_kana_chars.add(card.name_kana[0])

    return render_template(
        "cards/index.html",
        sections=sections,
        all_sections=all_sections,
        all_kana_chars=all_kana_chars,
        flat_cards=[],
        duplicate_ids=duplicate_ids,
        query=query,
        filter_type=filter_type,
        tag_filter=tag_filter,
        all_tags=all_tags,
        total_count=len(cards),
    )


def _get_kana_section(char):
    """カナ文字・アルファベットからセクション名を返す"""
    kana_groups = [
        ("ア", "アイウエオ"),
        ("カ", "カキクケコガギグゲゴ"),
        ("サ", "サシスセソザジズゼゾ"),
        ("タ", "タチツテトダヂヅデド"),
        ("ナ", "ナニヌネノ"),
        ("ハ", "ハヒフヘホバビブベボパピプペポ"),
        ("マ", "マミムメモ"),
        ("ヤ", "ヤユヨ"),
        ("ラ", "ラリルレロ"),
        ("ワ", "ワヲン"),
    ]
    for section_name, chars in kana_groups:
        if char in chars:
            return section_name
    # A-Z対応
    upper = char.upper()
    if "A" <= upper <= "Z":
        return upper
    return "その他"


# ========== アーカイブ一覧 ==========

@cards_bp.route("/cards/archived")
@login_required
def archived_cards():
    """アーカイブ済み名刺一覧"""
    cards_query = Card.query.filter(Card.is_archived == True)  # noqa: E712
    cards_query = cards_query.filter(
        or_(
            Card.registered_by == current_user.id,
            Card.visibility == "shared",
        )
    )
    cards = cards_query.order_by(Card.updated_at.desc()).all()
    return render_template("cards/archived.html", cards=cards, total_count=len(cards))


# ========== アーカイブ切替 ==========

@cards_bp.route("/cards/<int:card_id>/archive", methods=["POST"])
@login_required
def toggle_archive(card_id):
    """名刺のアーカイブ/アーカイブ解除"""
    card = Card.query.get_or_404(card_id)
    if card.registered_by != current_user.id and not current_user.is_admin:
        abort(403)

    card.is_archived = not card.is_archived
    db.session.commit()

    if card.is_archived:
        flash("名刺をアーカイブしました。", "info")
        return redirect(url_for("cards.index"))
    else:
        flash("名刺をアーカイブから戻しました。", "success")
        return redirect(url_for("cards.show_card", card_id=card.id))


# ========== 名刺登録 ==========

@cards_bp.route("/cards/new")
@login_required
def new_card():
    """名刺登録画面"""
    return render_template("cards/new.html")


@cards_bp.route("/cards/new", methods=["POST"])
@login_required
def create_card():
    """名刺登録（画像アップロード → OCR → 構造化 → 確認画面）"""
    front_file = request.files.get("front_image")
    back_file = request.files.get("back_image")
    visibility = request.form.get("visibility", "shared")

    if not front_file or not front_file.filename:
        flash("表面の画像またはPDFを選択してください。", "danger")
        return redirect(url_for("cards.new_card"))

    card_image_ids = []
    original_keys = {}  # side → R2キー（元画像）
    front_text = ""
    back_text = ""

    try:
        front_raw = front_file.read()
        is_pdf = (
            front_file.filename.lower().endswith(".pdf")
            or front_file.content_type == "application/pdf"
        )

        if is_pdf:
            # === PDF処理: ページを画像に変換 ===
            page_images = pdf_to_images(front_raw, max_pages=2)
            if not page_images:
                flash("PDFから画像を取得できませんでした。", "danger")
                return redirect(url_for("cards.new_card"))

            # 1ページ目 = 表面
            light_bytes = preprocess_image_light(page_images[0])
            front_bytes = preprocess_image(page_images[0])
            front_key = generate_object_key(current_user.id, "front", "front.jpg")
            orig_key = front_key.replace("_front.", "_front_original.")
            upload_image(front_bytes, front_key)
            upload_image(light_bytes, orig_key)
            original_keys["front"] = orig_key

            try:
                front_text = extract_text_from_image(front_bytes)
            except Exception as e:
                logger.error(f"表面OCRエラー: {e}")
                flash("画像の文字読取に失敗しました。手動で入力してください。", "warning")

            front_image = CardImage(
                side="front",
                r2_object_key=front_key,
                original_filename=front_file.filename,
                ocr_raw_text=front_text,
            )
            db.session.add(front_image)
            db.session.flush()
            card_image_ids.append(front_image.id)

            # 2ページ目 = 裏面（あれば）
            if len(page_images) >= 2:
                light_bytes_b = preprocess_image_light(page_images[1])
                back_bytes = preprocess_image(page_images[1])
                back_key = generate_object_key(current_user.id, "back", "back.jpg")
                orig_key_b = back_key.replace("_back.", "_back_original.")
                upload_image(back_bytes, back_key)
                upload_image(light_bytes_b, orig_key_b)
                original_keys["back"] = orig_key_b

                try:
                    back_text = extract_text_from_image(back_bytes)
                except Exception as e:
                    logger.error(f"裏面OCRエラー: {e}")

                back_image = CardImage(
                    side="back",
                    r2_object_key=back_key,
                    original_filename=front_file.filename,
                    ocr_raw_text=back_text,
                )
                db.session.add(back_image)
                db.session.flush()
                card_image_ids.append(back_image.id)

        else:
            # === 画像処理 ===
            light_bytes = preprocess_image_light(front_raw)
            front_bytes = preprocess_image(front_raw)
            front_key = generate_object_key(current_user.id, "front", front_file.filename)
            orig_key = front_key.replace("_front.", "_front_original.")
            upload_image(front_bytes, front_key)
            upload_image(light_bytes, orig_key)
            original_keys["front"] = orig_key

            try:
                front_text = extract_text_from_image(front_bytes)
            except Exception as e:
                logger.error(f"表面OCRエラー: {e}")
                flash("画像の文字読取に失敗しました。手動で入力してください。", "warning")

            front_image = CardImage(
                side="front",
                r2_object_key=front_key,
                original_filename=front_file.filename,
                ocr_raw_text=front_text,
            )
            db.session.add(front_image)
            db.session.flush()
            card_image_ids.append(front_image.id)

            # 裏面（あれば）
            if back_file and back_file.filename:
                back_raw = back_file.read()
                light_bytes_b = preprocess_image_light(back_raw)
                back_bytes = preprocess_image(back_raw)
                back_key = generate_object_key(current_user.id, "back", back_file.filename)
                orig_key_b = back_key.replace("_back.", "_back_original.")
                upload_image(back_bytes, back_key)
                upload_image(light_bytes_b, orig_key_b)
                original_keys["back"] = orig_key_b

                try:
                    back_text = extract_text_from_image(back_bytes)
                except Exception as e:
                    logger.error(f"裏面OCRエラー: {e}")

                back_image = CardImage(
                    side="back",
                    r2_object_key=back_key,
                    original_filename=back_file.filename,
                    ocr_raw_text=back_text,
                )
                db.session.add(back_image)
                db.session.flush()
                card_image_ids.append(back_image.id)

        db.session.commit()

    except Exception as e:
        db.session.rollback()
        logger.error(f"アップロードエラー: {e}")
        flash("アップロードに失敗しました。もう一度お試しください。", "danger")
        return redirect(url_for("cards.new_card"))

    # === Claude API 構造化 ===
    structured = {}
    if front_text:
        try:
            structured = structure_card_data(front_text, back_text or None)
        except Exception as e:
            logger.error(f"構造化エラー: {e}")
            flash("自動読取に失敗しました。手動で入力してください。", "warning")

    # セッションに一時保存して確認画面へ
    session["ocr_data"] = {
        "structured": structured,
        "card_image_ids": card_image_ids,
        "original_keys": original_keys,
        "visibility": visibility,
        "front_text": front_text,
        "back_text": back_text,
    }

    return redirect(url_for("cards.confirm_card"))


# ========== OCR確認画面 ==========

@cards_bp.route("/cards/confirm")
@login_required
def confirm_card():
    """OCR結果の確認・修正画面"""
    ocr_data = session.get("ocr_data")
    if not ocr_data:
        flash("セッションが切れました。もう一度名刺を登録してください。", "warning")
        return redirect(url_for("cards.new_card"))

    form_data = structured_to_form_data(ocr_data.get("structured", {}))

    # 画像の署名付きURLを取得
    image_urls = []
    for img_id in ocr_data.get("card_image_ids", []):
        img = CardImage.query.get(img_id)
        if img:
            try:
                url = get_presigned_url(img.r2_object_key)
                image_urls.append({"id": img.id, "side": img.side, "url": url})
            except Exception:
                image_urls.append({"id": img.id, "side": img.side, "url": None})

    has_originals = bool(ocr_data.get("original_keys"))

    return render_template(
        "cards/confirm.html",
        form_data=form_data,
        confirm_image_urls=image_urls,
        front_text=ocr_data.get("front_text", ""),
        back_text=ocr_data.get("back_text", ""),
        visibility=ocr_data.get("visibility", "shared"),
        has_originals=has_originals,
    )


@cards_bp.route("/cards/revert-image", methods=["POST"])
@login_required
def revert_image():
    """処理済み画像を元画像に差し替える"""
    ocr_data = session.get("ocr_data")
    if not ocr_data:
        return jsonify({"ok": False, "error": "セッション切れ"}), 400

    data = request.get_json()
    side = data.get("side")  # "front" or "back"
    original_keys = ocr_data.get("original_keys", {})
    orig_key = original_keys.get(side)
    if not orig_key:
        return jsonify({"ok": False, "error": "元画像がありません"}), 400

    # 対応するCardImageを取得
    img_id = None
    for cid in ocr_data.get("card_image_ids", []):
        img = CardImage.query.get(cid)
        if img and img.side == side:
            img_id = cid
            break

    if not img or not img_id:
        return jsonify({"ok": False, "error": "画像が見つかりません"}), 400

    try:
        # 元画像をダウンロードして処理済みキーに上書き
        orig_bytes = download_image(orig_key)
        upload_image(orig_bytes, img.r2_object_key)

        # OCR再実行
        try:
            new_text = extract_text_from_image(orig_bytes)
        except Exception:
            new_text = img.ocr_raw_text or ""

        img.ocr_raw_text = new_text
        db.session.commit()

        # セッションのOCRテキストも更新
        if side == "front":
            ocr_data["front_text"] = new_text
        else:
            ocr_data["back_text"] = new_text

        # 構造化を再実行
        front_text = ocr_data.get("front_text", "")
        back_text = ocr_data.get("back_text", "")
        if front_text:
            try:
                ocr_data["structured"] = structure_card_data(front_text, back_text or None)
            except Exception:
                pass
        session["ocr_data"] = ocr_data

        url = get_presigned_url(img.r2_object_key)
        form_data = structured_to_form_data(ocr_data.get("structured", {}))
        return jsonify({"ok": True, "url": url, "form_data": form_data})
    except Exception as e:
        logger.error(f"元画像差し替えエラー: {e}")
        return jsonify({"ok": False, "error": "処理に失敗しました"}), 500


@cards_bp.route("/cards/reprocess-image", methods=["POST"])
@login_required
def reprocess_image():
    """元画像からOpenCV処理を再実行して差し替える"""
    ocr_data = session.get("ocr_data")
    if not ocr_data:
        return jsonify({"ok": False, "error": "セッション切れ"}), 400

    data = request.get_json()
    side = data.get("side")
    original_keys = ocr_data.get("original_keys", {})
    orig_key = original_keys.get(side)
    if not orig_key:
        return jsonify({"ok": False, "error": "元画像がありません"}), 400

    img = None
    for cid in ocr_data.get("card_image_ids", []):
        img = CardImage.query.get(cid)
        if img and img.side == side:
            break

    if not img:
        return jsonify({"ok": False, "error": "画像が見つかりません"}), 400

    try:
        # 元画像をダウンロードして再処理
        orig_bytes = download_image(orig_key)
        processed_bytes = preprocess_image(orig_bytes)
        upload_image(processed_bytes, img.r2_object_key)

        # OCR再実行
        try:
            new_text = extract_text_from_image(processed_bytes)
        except Exception:
            new_text = img.ocr_raw_text or ""

        img.ocr_raw_text = new_text
        db.session.commit()

        if side == "front":
            ocr_data["front_text"] = new_text
        else:
            ocr_data["back_text"] = new_text

        front_text = ocr_data.get("front_text", "")
        back_text = ocr_data.get("back_text", "")
        if front_text:
            try:
                ocr_data["structured"] = structure_card_data(front_text, back_text or None)
            except Exception:
                pass
        session["ocr_data"] = ocr_data

        url = get_presigned_url(img.r2_object_key)
        form_data = structured_to_form_data(ocr_data.get("structured", {}))
        return jsonify({"ok": True, "url": url, "form_data": form_data})
    except Exception as e:
        logger.error(f"画像再処理エラー: {e}")
        return jsonify({"ok": False, "error": "処理に失敗しました"}), 500


@cards_bp.route("/cards/confirm", methods=["POST"])
@login_required
def save_card():
    """確認画面からのDB保存"""
    ocr_data = session.pop("ocr_data", None)
    if not ocr_data:
        flash("セッションが切れました。もう一度名刺を登録してください。", "warning")
        return redirect(url_for("cards.new_card"))

    # 会社マッチング
    company_name_ja = request.form.get("company_name_ja", "").strip()
    company_name_kana = request.form.get("company_name_kana", "").strip()
    company_id = match_or_create_company(company_name_ja, company_name_kana or None)

    # カード作成
    card = Card(
        company_id=company_id,
        registered_by=current_user.id,
        department=request.form.get("department", "").strip() or None,
        position=request.form.get("position", "").strip() or None,
        name_kanji=request.form.get("name_kanji", "").strip() or None,
        name_kana=request.form.get("name_kana", "").strip() or None,
        name_romaji=request.form.get("name_romaji", "").strip() or None,
        zip_code=request.form.get("zip_code", "").strip() or None,
        address=request.form.get("address", "").strip() or None,
        building=request.form.get("building", "").strip() or None,
        website=request.form.get("website", "").strip() or None,
        sns_info=request.form.get("sns_info", "").strip() or None,
        back_business_memo=request.form.get("back_business_memo", "").strip() or None,
        back_branch_memo=request.form.get("back_branch_memo", "").strip() or None,
        visibility=request.form.get("visibility", "shared"),
        memo=request.form.get("memo", "").strip() or None,
    )
    db.session.add(card)
    db.session.flush()

    # 電話番号
    phone_numbers = request.form.getlist("phone_number[]")
    phone_types = request.form.getlist("phone_type[]")
    for i, number in enumerate(phone_numbers):
        if number.strip():
            db.session.add(CardPhone(
                card_id=card.id,
                phone_number=number.strip(),
                phone_type=phone_types[i] if i < len(phone_types) else "main",
                sort_order=i,
            ))

    # メールアドレス
    email_addresses = request.form.getlist("email_address[]")
    email_types = request.form.getlist("email_type[]")
    for i, email in enumerate(email_addresses):
        if email.strip():
            db.session.add(CardEmail(
                card_id=card.id,
                email=email.strip(),
                email_type=email_types[i] if i < len(email_types) else "company",
                sort_order=i,
            ))

    # 資格
    qualifications = request.form.getlist("qualification[]")
    for i, qual in enumerate(qualifications):
        if qual.strip():
            db.session.add(CardQualification(
                card_id=card.id,
                qualification=qual.strip(),
                sort_order=i,
            ))

    # 画像をカードに紐付け（回転があれば適用）
    for img_id in ocr_data.get("card_image_ids", []):
        img = CardImage.query.get(img_id)
        if img:
            img.card_id = card.id
            # 手動回転が指定されている場合、R2の画像を回転して再保存
            rotation = int(request.form.get(f"{img.side}_rotation", 0))
            if rotation:
                try:
                    img_bytes = download_image(img.r2_object_key)
                    pil_img = PILImage.open(io.BytesIO(img_bytes))
                    pil_img = pil_img.rotate(-rotation, expand=True)
                    buf = io.BytesIO()
                    pil_img.save(buf, format="JPEG", quality=85)
                    upload_image(buf.getvalue(), img.r2_object_key)
                    logger.info(f"画像回転適用: {img.r2_object_key} ({rotation}°)")
                except Exception as e:
                    logger.error(f"画像回転エラー: {e}")

    db.session.commit()
    flash("名刺を登録しました。", "success")
    return redirect(url_for("cards.show_card", card_id=card.id))


# ========== 名刺詳細 ==========

@cards_bp.route("/cards/<int:card_id>")
@login_required
def show_card(card_id):
    """名刺詳細画面"""
    card = Card.query.get_or_404(card_id)

    # アクセス権チェック
    if card.visibility == "private" and card.registered_by != current_user.id:
        abort(403)

    # 画像URLを取得
    image_urls = []
    for img in card.images:
        try:
            url = get_presigned_url(img.r2_object_key)
            image_urls.append({"id": img.id, "side": img.side, "url": url})
        except Exception:
            image_urls.append({"id": img.id, "side": img.side, "url": None})

    all_tags = Tag.query.order_by(Tag.name).all()
    return render_template("cards/show.html", card=card, image_urls=image_urls, all_tags=all_tags)


# ========== 名刺編集 ==========

@cards_bp.route("/cards/<int:card_id>/edit")
@login_required
def edit_card(card_id):
    """名刺編集画面"""
    card = Card.query.get_or_404(card_id)
    if card.registered_by != current_user.id and not current_user.is_admin:
        abort(403)

    # 画像URLを取得
    image_urls = []
    for img in card.images:
        try:
            url = get_presigned_url(img.r2_object_key)
            image_urls.append({"id": img.id, "side": img.side, "url": url})
        except Exception:
            image_urls.append({"id": img.id, "side": img.side, "url": None})

    return render_template("cards/edit.html", card=card, image_urls=image_urls)


@cards_bp.route("/cards/<int:card_id>/edit", methods=["POST"])
@login_required
def update_card(card_id):
    """名刺更新"""
    card = Card.query.get_or_404(card_id)
    if card.registered_by != current_user.id and not current_user.is_admin:
        abort(403)

    # 会社マッチング（変更前のIDを記録）
    old_company_id = card.company_id
    company_name_ja = request.form.get("company_name_ja", "").strip()
    company_name_kana = request.form.get("company_name_kana", "").strip()
    card.company_id = match_or_create_company(company_name_ja, company_name_kana or None)

    # 基本情報更新
    card.department = request.form.get("department", "").strip() or None
    card.position = request.form.get("position", "").strip() or None
    card.name_kanji = request.form.get("name_kanji", "").strip() or None
    card.name_kana = request.form.get("name_kana", "").strip() or None
    card.name_romaji = request.form.get("name_romaji", "").strip() or None
    card.zip_code = request.form.get("zip_code", "").strip() or None
    card.address = request.form.get("address", "").strip() or None
    card.building = request.form.get("building", "").strip() or None
    card.website = request.form.get("website", "").strip() or None
    card.sns_info = request.form.get("sns_info", "").strip() or None
    card.back_business_memo = request.form.get("back_business_memo", "").strip() or None
    card.back_branch_memo = request.form.get("back_branch_memo", "").strip() or None
    card.visibility = request.form.get("visibility", "shared")
    card.memo = request.form.get("memo", "").strip() or None

    # 電話番号: 既存を削除して再作成
    CardPhone.query.filter_by(card_id=card.id).delete()
    phone_numbers = request.form.getlist("phone_number[]")
    phone_types = request.form.getlist("phone_type[]")
    for i, number in enumerate(phone_numbers):
        if number.strip():
            db.session.add(CardPhone(
                card_id=card.id,
                phone_number=number.strip(),
                phone_type=phone_types[i] if i < len(phone_types) else "main",
                sort_order=i,
            ))

    # メール: 既存を削除して再作成
    CardEmail.query.filter_by(card_id=card.id).delete()
    email_addresses = request.form.getlist("email_address[]")
    email_types = request.form.getlist("email_type[]")
    for i, email in enumerate(email_addresses):
        if email.strip():
            db.session.add(CardEmail(
                card_id=card.id,
                email=email.strip(),
                email_type=email_types[i] if i < len(email_types) else "company",
                sort_order=i,
            ))

    # 資格: 既存を削除して再作成
    CardQualification.query.filter_by(card_id=card.id).delete()
    qualifications = request.form.getlist("qualification[]")
    for i, qual in enumerate(qualifications):
        if qual.strip():
            db.session.add(CardQualification(
                card_id=card.id,
                qualification=qual.strip(),
                sort_order=i,
            ))

    # 会社が変わった場合、旧会社の名刺が0件なら自動削除
    if old_company_id and old_company_id != card.company_id:
        _cleanup_empty_company(old_company_id)

    db.session.commit()
    flash("名刺を更新しました。", "success")
    return redirect(url_for("cards.show_card", card_id=card.id))


def _cleanup_empty_company(company_id):
    """名刺が0件になった会社を自動削除する"""
    if not company_id:
        return
    company = Company.query.get(company_id)
    if not company:
        return
    remaining = Card.query.filter_by(company_id=company_id).count()
    if remaining == 0:
        # 統合先として参照されている場合はスキップ
        merged_refs = Company.query.filter_by(merged_into_id=company_id).count()
        if merged_refs == 0:
            logger.info(f"会社自動削除: {company.name_ja} (ID:{company_id})")
            db.session.delete(company)


# ========== 名刺削除 ==========

@cards_bp.route("/cards/<int:card_id>/delete", methods=["POST"])
@login_required
def delete_card(card_id):
    """名刺削除"""
    card = Card.query.get_or_404(card_id)
    if card.registered_by != current_user.id and not current_user.is_admin:
        abort(403)

    company_id = card.company_id

    # R2の画像も削除
    for img in card.images:
        try:
            delete_image(img.r2_object_key)
        except Exception as e:
            logger.error(f"R2画像削除エラー: {e}")

    db.session.delete(card)
    db.session.flush()

    # 名刺が0件になった会社を自動削除
    _cleanup_empty_company(company_id)

    db.session.commit()
    flash("名刺を削除しました。", "info")
    return redirect(url_for("cards.index"))


# ========== 画像表示（署名付きURLリダイレクト） ==========

@cards_bp.route("/cards/image/<int:image_id>")
@login_required
def card_image(image_id):
    """名刺画像の署名付きURLにリダイレクト"""
    image = CardImage.query.get_or_404(image_id)

    # card_idが未設定（確認画面前）の場合はそのまま表示許可
    if image.card_id:
        card = Card.query.get_or_404(image.card_id)
        if card.visibility == "private" and card.registered_by != current_user.id:
            abort(403)

    url = get_presigned_url(image.r2_object_key, expires_in=3600)
    return redirect(url)


# ========== タグ管理（API） ==========

@cards_bp.route("/cards/<int:card_id>/tags", methods=["POST"])
@login_required
def add_tag(card_id):
    """名刺にタグを追加"""
    card = Card.query.get_or_404(card_id)
    data = request.get_json()
    name = (data.get("name") or "").strip()

    if not name:
        return jsonify({"ok": False, "error": "タグ名を入力してください"}), 400

    # 既存タグを検索 or 新規作成
    tag = Tag.query.filter_by(name=name).first()
    if not tag:
        tag = Tag(name=name)
        db.session.add(tag)
        db.session.flush()

    # 既に紐付いていないか確認
    if tag in card.tags:
        return jsonify({"ok": False, "error": "このタグは既に追加されています"}), 400

    card.tags.append(tag)
    db.session.commit()
    return jsonify({"ok": True, "id": tag.id, "name": tag.name})


@cards_bp.route("/cards/<int:card_id>/tags/<int:tag_id>", methods=["DELETE"])
@login_required
def remove_tag(card_id, tag_id):
    """名刺からタグを削除"""
    card = Card.query.get_or_404(card_id)
    tag = Tag.query.get_or_404(tag_id)

    if tag in card.tags:
        card.tags.remove(tag)
        db.session.commit()

    return jsonify({"ok": True})
