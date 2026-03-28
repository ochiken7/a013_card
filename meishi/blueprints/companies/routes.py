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

    # 統合済みの会社
    merged = Company.query.filter(Company.merged_into_id.isnot(None)).all()

    return render_template(
        "companies/index.html",
        sections=sections,
        all_sections=all_sections,
        companies=companies,
        merged=merged,
    )


@companies_bp.route("/companies/<int:company_id>/cards")
@login_required
def company_cards(company_id):
    """会社に属する名刺一覧"""
    company = Company.query.get_or_404(company_id)
    cards = Card.query.filter_by(company_id=company_id).order_by(Card.name_kana.asc()).all()
    return render_template("companies/cards.html", company=company, cards=cards)


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
