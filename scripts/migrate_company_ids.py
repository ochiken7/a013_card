#!/usr/bin/env python
"""
会社IDの再採番マイグレーション:
- a001に名前一致する会社 → a001のIDに揃える
- それ以外 → 現IDに +100000 シフト
- companies.id シーケンス開始値を 100000 に変更
"""
import sys
sys.path.insert(0, '/var/www/vpsk/a013')

from meishi import create_app, db
from meishi.models.company import Company
from meishi.services.a001_api import search_clients
from meishi.services.company_matcher import normalize_company_name

app = create_app()

with app.app_context():
    # ---- Step 1: a001 全クライアント取得 ----
    print("=== Step 1: a001クライアント取得 ===")
    a001_clients = search_clients("")  # 空検索=全件
    print(f"a001: {len(a001_clients)} 件")

    # 正規化名 → a001 ID のマップ（重複名は最初のもの採用）
    a001_name_to_id = {}
    for client in a001_clients:
        name = client.get("cl_name", "")
        normalized = normalize_company_name(name)
        if normalized and normalized not in a001_name_to_id:
            a001_name_to_id[normalized] = client.get("id")
    print(f"a001 正規化ユニーク名: {len(a001_name_to_id)}")

    # ---- Step 2: a013会社マッピング構築 ----
    print("\n=== Step 2: a013マッピング構築 ===")
    a013_companies = Company.query.all()
    print(f"a013: {len(a013_companies)} 件")

    id_mapping = {}  # 旧ID → 新ID
    used_new_ids = set()
    matched = 0

    for company in a013_companies:
        normalized = normalize_company_name(company.name_ja or "")
        a001_id = a001_name_to_id.get(normalized)
        if a001_id and a001_id not in used_new_ids:
            id_mapping[company.id] = a001_id
            used_new_ids.add(a001_id)
            matched += 1
        else:
            new_id = company.id + 100000
            id_mapping[company.id] = new_id
            used_new_ids.add(new_id)

    print(f"  a001マッチ: {matched} 件")
    print(f"  +100000シフト: {len(a013_companies) - matched} 件")

    # 重複チェック
    if len(set(id_mapping.values())) != len(id_mapping):
        print("ERROR: 新IDに重複あり。中止します。")
        sys.exit(1)

    # ---- Step 3: DB更新 ----
    print("\n=== Step 3: DB更新 ===")

    # FK制約を DEFERRABLE に
    db.session.execute(db.text(
        "ALTER TABLE cards ALTER CONSTRAINT cards_company_id_fkey DEFERRABLE INITIALLY DEFERRED"
    ))
    db.session.execute(db.text(
        "ALTER TABLE companies ALTER CONSTRAINT companies_merged_into_id_fkey DEFERRABLE INITIALLY DEFERRED"
    ))
    db.session.execute(db.text("SET CONSTRAINTS ALL DEFERRED"))

    # Step 3a: 全IDを負値に退避（衝突回避）
    print("  Step 3a: 一時ID(負値)に退避...")
    db.session.execute(db.text("UPDATE companies SET id = -id"))
    db.session.execute(db.text(
        "UPDATE cards SET company_id = -company_id WHERE company_id IS NOT NULL"
    ))
    db.session.execute(db.text(
        "UPDATE companies SET merged_into_id = -merged_into_id WHERE merged_into_id IS NOT NULL"
    ))

    # Step 3b: 新IDへ更新
    print("  Step 3b: 新IDへ更新...")
    for old_id, new_id in id_mapping.items():
        temp_id = -old_id
        db.session.execute(
            db.text("UPDATE companies SET id = :new WHERE id = :old"),
            {"new": new_id, "old": temp_id},
        )
        db.session.execute(
            db.text("UPDATE cards SET company_id = :new WHERE company_id = :old"),
            {"new": new_id, "old": temp_id},
        )
        db.session.execute(
            db.text("UPDATE companies SET merged_into_id = :new WHERE merged_into_id = :old"),
            {"new": new_id, "old": temp_id},
        )

    db.session.commit()

    # FK制約を NOT DEFERRABLE に戻す
    db.session.execute(db.text(
        "ALTER TABLE cards ALTER CONSTRAINT cards_company_id_fkey NOT DEFERRABLE"
    ))
    db.session.execute(db.text(
        "ALTER TABLE companies ALTER CONSTRAINT companies_merged_into_id_fkey NOT DEFERRABLE"
    ))
    db.session.commit()

    # ---- Step 4: シーケンス調整 ----
    print("\n=== Step 4: シーケンス調整 ===")
    max_id = db.session.query(db.func.max(Company.id)).scalar() or 0
    new_seq_start = max(100000, max_id + 1)
    db.session.execute(db.text(
        f"SELECT setval('companies_id_seq', {new_seq_start}, false)"
    ))
    db.session.commit()
    print(f"  次の自動採番値: {new_seq_start}")

    print("\n=== 完了 ===")
    print(f"最大会社ID: {max_id}")
