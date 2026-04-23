"""CSV入出力（エクスポート・インポート）"""

import csv
import io
from datetime import datetime

from flask import render_template, request, redirect, url_for, flash, Response
from flask_login import login_required, current_user
from sqlalchemy import or_

from meishi import db
from meishi.models.card import Card, CardPhone, CardEmail, CardQualification
from meishi.models.company import Company
from meishi.services.company_matcher import match_or_create_company
from meishi.blueprints.csv_io import csv_bp


# --- CSV画面 ---
@csv_bp.route("/csv")
@login_required
def index():
    """CSV入出力画面"""
    return render_template("csv/index.html")


# --- エクスポート ---
@csv_bp.route("/csv/export")
@login_required
def export():
    """名刺データをCSVファイルとしてダウンロード"""
    # 自分の名刺 + 共有名刺を取得
    cards = Card.query.filter(
        or_(Card.registered_by == current_user.id, Card.visibility == "shared")
    ).order_by(Card.name_kana.asc()).all()

    # CSV出力（BOM付きUTF-8でExcel対応）
    output = io.StringIO()
    output.write("\ufeff")  # UTF-8 BOM
    writer = csv.writer(output)

    # ヘッダー行
    writer.writerow([
        "id", "company_name_ja", "company_name_kana",
        "department", "position",
        "name_kanji", "name_kana", "name_romaji",
        "phone_numbers", "email_addresses", "qualifications",
        "zip_code", "address", "building",
        "website", "sns_info", "memo",
        "visibility", "created_at",
    ])

    # データ行
    for card in cards:
        phones = ";".join([p.phone_number for p in card.phones])
        emails = ";".join([e.email for e in card.emails])
        quals = ";".join([q.qualification for q in card.qualifications])

        writer.writerow([
            card.id,
            card.company.name_ja if card.company else "",
            card.company.name_kana if card.company else "",
            card.department or "",
            card.position or "",
            card.name_kanji or "",
            card.name_kana or "",
            card.name_romaji or "",
            phones,
            emails,
            quals,
            card.zip_code or "",
            card.address or "",
            card.building or "",
            card.website or "",
            card.sns_info or "",
            card.memo or "",
            card.visibility or "",
            card.created_at.strftime("%Y-%m-%d %H:%M:%S") if card.created_at else "",
        ])

    today = datetime.now().strftime("%Y%m%d")
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=meishi_export_{today}.csv"
        },
    )


# --- インポート ---
@csv_bp.route("/csv/import", methods=["POST"])
@login_required
def import_csv():
    """CSVファイルから名刺データを一括登録"""
    file = request.files.get("csv_file")
    if not file or file.filename == "":
        flash("CSVファイルを選択してください。", "warning")
        return redirect(url_for("csv_io.index"))

    if not file.filename.lower().endswith(".csv"):
        flash("CSVファイル（.csv）を選択してください。", "warning")
        return redirect(url_for("csv_io.index"))

    try:
        # ファイル読み込み（BOM対応）
        raw = file.read()
        text = raw.decode("utf-8-sig")
        reader = csv.reader(io.StringIO(text))

        # ヘッダー行をスキップ
        header = next(reader, None)
        if header is None:
            flash("CSVファイルが空です。", "warning")
            return redirect(url_for("csv_io.index"))

        imported_count = 0

        for row in reader:
            if not row or len(row) < 18:
                continue  # 列数不足の行はスキップ

            # CSV列の対応（idは無視して新規作成）
            (
                _id,
                company_name_ja, company_name_kana,
                department, position,
                name_kanji, name_kana, name_romaji,
                phone_numbers, email_addresses, qualifications,
                zip_code, address, building,
                website, sns_info, memo,
                visibility,
                *_rest,
            ) = row

            # 会社マッチング
            company_id = match_or_create_company(
                company_name_ja.strip() if company_name_ja.strip() else None,
                company_name_kana.strip() if company_name_kana.strip() else None,
            )

            # 名刺レコード作成
            card = Card(
                company_id=company_id,
                registered_by=current_user.id,
                department=department.strip() or None,
                position=position.strip() or None,
                name_kanji=name_kanji.strip() or None,
                name_kana=name_kana.strip() or None,
                name_romaji=name_romaji.strip() or None,
                zip_code=zip_code.strip() or None,
                address=address.strip() or None,
                building=building.strip() or None,
                website=website.strip() or None,
                sns_info=sns_info.strip() or None,
                memo=memo.strip() or None,
                visibility=visibility.strip() if visibility.strip() in ("private", "shared") else "private",
            )
            db.session.add(card)
            db.session.flush()  # card.id を確定

            # 電話番号（セミコロン区切り）
            if phone_numbers.strip():
                for i, phone in enumerate(phone_numbers.split(";")):
                    phone = phone.strip()
                    if phone:
                        db.session.add(CardPhone(
                            card_id=card.id,
                            phone_number=phone,
                            phone_type="main",
                            sort_order=i,
                        ))

            # メールアドレス（セミコロン区切り）
            if email_addresses.strip():
                for i, email in enumerate(email_addresses.split(";")):
                    email = email.strip()
                    if email:
                        db.session.add(CardEmail(
                            card_id=card.id,
                            email=email,
                            email_type="company",
                            sort_order=i,
                        ))

            # 資格（セミコロン区切り）
            if qualifications.strip():
                for i, qual in enumerate(qualifications.split(";")):
                    qual = qual.strip()
                    if qual:
                        db.session.add(CardQualification(
                            card_id=card.id,
                            qualification=qual,
                            sort_order=i,
                        ))

            imported_count += 1

        db.session.commit()
        flash(f"{imported_count}件の名刺をインポートしました。", "success")

    except Exception as e:
        db.session.rollback()
        flash(f"インポートに失敗しました: {str(e)}", "danger")

    return redirect(url_for("csv_io.index"))


@csv_bp.route("/csv/update-names", methods=["POST"])
@login_required
def update_names():
    """CSVのidに一致する名刺の氏名・フリガナのみ上書き更新"""
    file = request.files.get("csv_file")
    if not file or file.filename == "":
        flash("CSVファイルを選択してください。", "warning")
        return redirect(url_for("csv_io.index"))

    try:
        raw = file.read()
        text = raw.decode("utf-8-sig")
        reader = csv.reader(io.StringIO(text))

        header = next(reader, None)
        if header is None:
            flash("CSVファイルが空です。", "warning")
            return redirect(url_for("csv_io.index"))

        updated_count = 0

        for row in reader:
            if not row or len(row) < 7:
                continue

            card_id = row[0].strip()
            if not card_id.isdigit():
                continue

            name_kanji = row[5].strip() if len(row) > 5 else ""
            name_kana = row[6].strip() if len(row) > 6 else ""

            card = Card.query.get(int(card_id))
            if not card:
                continue

            card.name_kanji = name_kanji or None
            card.name_kana = name_kana or None
            updated_count += 1

        db.session.commit()
        flash(f"{updated_count}件の氏名・フリガナを更新しました。", "success")

    except Exception as e:
        db.session.rollback()
        flash(f"更新に失敗しました: {str(e)}", "danger")

    return redirect(url_for("csv_io.index"))


@csv_bp.route("/csv/update-companies", methods=["POST"])
@login_required
def update_companies():
    """CSVのidに一致する名刺の会社名・会社名フリガナを上書き更新"""
    file = request.files.get("csv_file")
    if not file or file.filename == "":
        flash("CSVファイルを選択してください。", "warning")
        return redirect(url_for("csv_io.index"))

    try:
        raw = file.read()
        text = raw.decode("utf-8-sig")
        reader = csv.reader(io.StringIO(text))

        header = next(reader, None)
        if header is None:
            flash("CSVファイルが空です。", "warning")
            return redirect(url_for("csv_io.index"))

        updated_companies = set()

        for row in reader:
            if not row or len(row) < 3:
                continue

            card_id = row[0].strip()
            if not card_id.isdigit():
                continue

            company_name_ja = row[1].strip() if len(row) > 1 else ""
            company_name_kana = row[2].strip() if len(row) > 2 else ""

            card = Card.query.get(int(card_id))
            if not card or not card.company:
                continue

            # 同じ会社は1度だけ更新
            if card.company.id in updated_companies:
                continue

            if company_name_ja:
                card.company.name_ja = company_name_ja
            card.company.name_kana = company_name_kana or None
            updated_companies.add(card.company.id)

        db.session.commit()
        flash(f"{len(updated_companies)}件の会社情報を更新しました。", "success")

    except Exception as e:
        db.session.rollback()
        flash(f"更新に失敗しました: {str(e)}", "danger")

    return redirect(url_for("csv_io.index"))
