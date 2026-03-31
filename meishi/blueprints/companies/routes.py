"""会社グルーピング管理"""

from flask import render_template, redirect, url_for, flash, request
from flask_login import login_required
from sqlalchemy import func
from meishi import db
from meishi.blueprints.companies import companies_bp
from meishi.models.company import Company
from meishi.models.card import Card


def _get_company_section(company):
    """会社のフリガナからセクション名を返す"""
    kana = company.name_kana
    if not kana:
        return "その他"
    char = kana[0]
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
    upper = char.upper()
    if "A" <= upper <= "Z":
        return upper
    return "その他"


@companies_bp.route("/companies")
@login_required
def index():
    """会社一覧（名刺件数付き・五十音セクション）"""
    # 有効な会社のみ（統合済み除外）+ 名刺件数
    companies = (
        db.session.query(Company, func.count(Card.id).label("card_count"))
        .outerjoin(Card, Card.company_id == Company.id)
        .filter(Company.merged_into_id.is_(None))
        .group_by(Company.id)
        .order_by(Company.name_kana.asc().nullslast(), Company.name_ja.asc())
        .all()
    )

    # 五十音セクション分け
    sections = {}
    for company, count in companies:
        section = _get_company_section(company)
        sections.setdefault(section, []).append((company, count))

    all_sections = list(sections.keys())

    # 各会社のフリガナ先頭文字を集める（ボタンのハイライト用）
    all_kana_chars = set()
    for company, _count in companies:
        if company.name_kana:
            all_kana_chars.add(company.name_kana[0])

    # 統合済みの会社
    merged = Company.query.filter(Company.merged_into_id.isnot(None)).all()

    return render_template(
        "companies/index.html",
        sections=sections,
        all_sections=all_sections,
        all_kana_chars=all_kana_chars,
        companies=companies,
        merged=merged,
    )


def _position_sort_key(card):
    """役職の優先度を返す（小さいほど上に表示）"""
    position = card.position or ""
    priority_list = [
        "代表取締役",
        "取締役",
        "執行役員",
    ]
    for i, keyword in enumerate(priority_list):
        if keyword in position:
            return (i, card.name_kana or "")
    return (len(priority_list), card.name_kana or "")


@companies_bp.route("/companies/<int:company_id>/cards")
@login_required
def company_cards(company_id):
    """会社に属する名刺一覧（役職順）"""
    company = Company.query.get(company_id)
    if not company:
        return render_template("companies/not_found.html", company_id=company_id), 404
    cards = Card.query.filter_by(company_id=company_id).all()
    cards.sort(key=_position_sort_key)
    return render_template("companies/cards.html", company=company, cards=cards)


@companies_bp.route("/companies/merge", methods=["GET"])
@login_required
def merge_form():
    """会社統合画面"""
    companies = (
        db.session.query(Company, func.count(Card.id).label("card_count"))
        .outerjoin(Card, Card.company_id == Company.id)
        .filter(Company.merged_into_id.is_(None))
        .group_by(Company.id)
        .order_by(Company.name_kana.asc().nullslast(), Company.name_ja.asc())
        .all()
    )
    merged = Company.query.filter(Company.merged_into_id.isnot(None)).all()
    return render_template("companies/merge.html", companies=companies, merged=merged)


@companies_bp.route("/companies/merge", methods=["POST"])
@login_required
def merge():
    """会社統合（source → target に統合）"""
    source_id = request.form.get("source_id", type=int)
    target_id = request.form.get("target_id", type=int)

    if not source_id or not target_id or source_id == target_id:
        flash("統合元と統合先を正しく選択してください。", "danger")
        return redirect(url_for("companies.index"))

    source = Company.query.get_or_404(source_id)
    target = Company.query.get_or_404(target_id)

    # sourceの名刺をtargetに移動
    Card.query.filter_by(company_id=source_id).update({"company_id": target_id})
    source.merged_into_id = target_id
    db.session.commit()

    flash(f"「{source.name_ja}」を「{target.name_ja}」に統合しました。", "success")
    return redirect(url_for("companies.index"))


@companies_bp.route("/companies/<int:company_id>/change-id", methods=["POST"])
@login_required
def change_id(company_id):
    """会社IDを変更"""
    new_id = request.form.get("new_id", type=int)
    if not new_id or new_id < 1:
        flash("正しいID（1以上の整数）を入力してください。", "danger")
        return redirect(url_for("companies.company_cards", company_id=company_id))

    if new_id == company_id:
        flash("現在と同じIDです。", "warning")
        return redirect(url_for("companies.company_cards", company_id=company_id))

    # 既に使われていないか確認
    existing = Company.query.get(new_id)
    if existing:
        flash(f"ID {new_id} は既に「{existing.name_ja}」で使われています。", "danger")
        return redirect(url_for("companies.company_cards", company_id=company_id))

    company = Company.query.get_or_404(company_id)
    # セッションから切り離す（生SQLでID変更するとORM側と矛盾するため）
    db.session.expunge(company)

    # FK制約を一時的にDEFERRABLEにしてからID変更
    db.session.execute(db.text(
        "ALTER TABLE cards ALTER CONSTRAINT cards_company_id_fkey DEFERRABLE INITIALLY DEFERRED"
    ))
    db.session.execute(db.text(
        "ALTER TABLE companies ALTER CONSTRAINT companies_merged_into_id_fkey DEFERRABLE INITIALLY DEFERRED"
    ))
    db.session.execute(db.text("SET CONSTRAINTS ALL DEFERRED"))

    # 1. company自体のIDを変更
    db.session.execute(
        db.text("UPDATE companies SET id = :new WHERE id = :old"),
        {"new": new_id, "old": company_id},
    )
    # 2. cardsのcompany_idを更新
    db.session.execute(
        db.text("UPDATE cards SET company_id = :new WHERE company_id = :old"),
        {"new": new_id, "old": company_id},
    )
    # 3. companiesのmerged_into_idを更新
    db.session.execute(
        db.text("UPDATE companies SET merged_into_id = :new WHERE merged_into_id = :old"),
        {"new": new_id, "old": company_id},
    )
    db.session.commit()

    # FK制約を元に戻す（IMMEDIATE）
    db.session.execute(db.text(
        "ALTER TABLE cards ALTER CONSTRAINT cards_company_id_fkey NOT DEFERRABLE"
    ))
    db.session.execute(db.text(
        "ALTER TABLE companies ALTER CONSTRAINT companies_merged_into_id_fkey NOT DEFERRABLE"
    ))
    db.session.commit()

    flash(f"会社IDを {company_id} → {new_id} に変更しました。", "success")
    return redirect(url_for("companies.company_cards", company_id=new_id))


@companies_bp.route("/companies/<int:company_id>/unmerge", methods=["POST"])
@login_required
def unmerge(company_id):
    """会社統合を解除"""
    company = Company.query.get_or_404(company_id)
    if not company.merged_into_id:
        flash("この会社は統合されていません。", "warning")
        return redirect(url_for("companies.index"))

    company.merged_into_id = None
    db.session.commit()
    flash(f"「{company.name_ja}」の統合を解除しました。", "success")
    return redirect(url_for("companies.index"))
