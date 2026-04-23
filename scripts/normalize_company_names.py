#!/usr/bin/env python
"""
会社名に含まれる半角英数字を全角に変換
例: 'ABC商事' → 'ＡＢＣ商事' / 'KANADE123' → 'ＫＡＮＡＤＥ１２３'
"""
import sys
sys.path.insert(0, '/var/www/vpsk/a013')

from meishi import create_app, db
from meishi.models.company import Company

app = create_app()


def to_fullwidth(text):
    """半角英数字を全角に変換"""
    if not text:
        return text
    result = []
    for ch in text:
        code = ord(ch)
        # 半角英数字 (0-9, A-Z, a-z) を全角に
        if 0x30 <= code <= 0x39 or 0x41 <= code <= 0x5A or 0x61 <= code <= 0x7A:
            result.append(chr(code + 0xFEE0))
        else:
            result.append(ch)
    return "".join(result)


with app.app_context():
    companies = Company.query.all()
    print(f"会社数: {len(companies)} 件")

    changed_count = 0
    for company in companies:
        original = company.name_ja or ""
        normalized = to_fullwidth(original)
        if original != normalized:
            print(f"  [{company.id}] {original} → {normalized}")
            company.name_ja = normalized
            changed_count += 1

    if changed_count > 0:
        db.session.commit()
        print(f"\n完了: {changed_count} 件を更新しました。")
    else:
        print("\n変換対象なし。")
