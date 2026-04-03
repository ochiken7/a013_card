"""会社名の正規化・マッチング"""

import re
from meishi import db
from meishi.models.company import Company
from meishi.services.a001_api import search_clients


def normalize_company_name(name):
    """会社名の表記揺れを正規化"""
    if not name:
        return ""
    name = name.strip()
    name = name.replace("\u3000", " ")  # 全角→半角スペース
    # （株）→ 株式会社 等の正規化
    name = re.sub(r"[（\(]株[）\)]", "株式会社", name)
    name = re.sub(r"[（\(]有[）\)]", "有限会社", name)
    name = re.sub(r"[（\(]合[）\)]", "合同会社", name)
    # 連続スペースを1つに
    name = re.sub(r"\s+", " ", name)
    return name


def match_or_create_company(company_name_ja, company_name_kana=None):
    """会社名でDBを検索し、一致すればそのIDを返す。なければa001を検索してID引用。"""
    if not company_name_ja:
        return None

    normalized = normalize_company_name(company_name_ja)

    # 既存の会社を検索（統合済みは除外）
    companies = Company.query.filter(
        Company.merged_into_id.is_(None)
    ).all()

    for company in companies:
        if normalize_company_name(company.name_ja) == normalized:
            return company.id

    # a001の顧客システムからIDを取得
    a001_id = _find_a001_client_id(normalized)

    if a001_id:
        # a001のIDが既にa013で使われていないか確認
        existing = Company.query.get(a001_id)
        if existing:
            # IDが使われている場合はa001のIDは使えないので通常の新規作成
            company = Company(name_ja=company_name_ja, name_kana=company_name_kana)
            db.session.add(company)
            db.session.flush()
            return company.id

        # a001のIDを使って新規作成
        company = Company(id=a001_id, name_ja=company_name_ja, name_kana=company_name_kana)
        db.session.add(company)
        db.session.flush()
        return company.id

    # a001に見つからなければ通常の新規作成
    company = Company(name_ja=company_name_ja, name_kana=company_name_kana)
    db.session.add(company)
    db.session.flush()
    return company.id


def _find_a001_client_id(normalized_name):
    """a001 APIで会社名を検索し、完全一致するクライアントのIDを返す"""
    try:
        clients = search_clients(normalized_name)
    except Exception:
        return None

    for client in clients:
        a001_name = normalize_company_name(client.get("cl_name", ""))
        if a001_name == normalized_name:
            return client.get("id")

    return None
