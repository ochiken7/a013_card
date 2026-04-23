"""ユーザー管理・タグ管理（管理者のみ）"""

from functools import wraps
from flask import render_template, redirect, url_for, flash, request, abort, jsonify
from flask_login import login_required, current_user
from meishi import db
from meishi.blueprints.admin import admin_bp
from sqlalchemy import func
from meishi.models.user import User
from meishi.models.tag import Tag, CardTag
from meishi.models.company import Company
from meishi.models.card import Card


def admin_required(f):
    """管理者権限チェックデコレータ"""
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return decorated


@admin_bp.route("/admin/users")
@admin_required
def user_list():
    """ユーザー一覧"""
    users = User.query.order_by(User.created_at.desc()).all()
    return render_template("admin/users.html", users=users)


@admin_bp.route("/admin/users/new", methods=["GET", "POST"])
@admin_required
def create_user():
    """新規ユーザー登録"""
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        display_name = request.form.get("display_name", "").strip()
        password = request.form.get("password", "")
        is_admin = bool(request.form.get("is_admin"))

        if not email or not display_name or not password:
            flash("すべての項目を入力してください。", "danger")
            return render_template("admin/user_form.html")

        if User.query.filter_by(email=email).first():
            flash("このメールアドレスは既に登録されています。", "danger")
            return render_template("admin/user_form.html")

        user = User(email=email, display_name=display_name, is_admin=is_admin)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        flash(f"ユーザー「{display_name}」を登録しました。", "success")
        return redirect(url_for("admin.user_list"))

    return render_template("admin/user_form.html")


@admin_bp.route("/admin/users/<int:user_id>/reset-password", methods=["POST"])
@admin_required
def reset_password(user_id):
    """パスワードリセット"""
    user = User.query.get_or_404(user_id)
    new_password = request.form.get("new_password", "")
    if not new_password:
        flash("新しいパスワードを入力してください。", "danger")
        return redirect(url_for("admin.user_list"))

    user.set_password(new_password)
    db.session.commit()
    flash(f"「{user.display_name}」のパスワードをリセットしました。", "success")
    return redirect(url_for("admin.user_list"))


@admin_bp.route("/admin/users/<int:user_id>/toggle-admin", methods=["POST"])
@admin_required
def toggle_admin(user_id):
    """管理者権限の切替"""
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash("自分自身の管理者権限は変更できません。", "danger")
        return redirect(url_for("admin.user_list"))

    user.is_admin = not user.is_admin
    db.session.commit()
    status = "付与" if user.is_admin else "解除"
    flash(f"「{user.display_name}」の管理者権限を{status}しました。", "success")
    return redirect(url_for("admin.user_list"))


@admin_bp.route("/admin/users/<int:user_id>/edit", methods=["POST"])
@admin_required
def edit_user(user_id):
    """ユーザー情報の編集（名前・メール）"""
    user = User.query.get_or_404(user_id)
    display_name = request.form.get("display_name", "").strip()
    email = request.form.get("email", "").strip()

    if not display_name or not email:
        flash("名前とメールアドレスは必須です。", "danger")
        return redirect(url_for("admin.user_list"))

    # メール重複チェック
    existing = User.query.filter_by(email=email).first()
    if existing and existing.id != user.id:
        flash("このメールアドレスは既に使われています。", "danger")
        return redirect(url_for("admin.user_list"))

    old_name = user.display_name
    user.display_name = display_name
    user.email = email
    db.session.commit()
    flash(f"「{old_name}」のユーザー情報を更新しました。", "success")
    return redirect(url_for("admin.user_list"))


@admin_bp.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@admin_required
def delete_user(user_id):
    """ユーザー削除（自分以外のみ）"""
    if user_id == current_user.id:
        flash("自分自身は削除できません。", "danger")
        return redirect(url_for("admin.user_list"))

    user = User.query.get_or_404(user_id)
    name = user.display_name

    # 名刺の registered_by を管理者に移行してから削除
    from meishi.models.card import Card
    Card.query.filter_by(registered_by=user.id).update({"registered_by": current_user.id})
    db.session.flush()  # 移行を確定させてからユーザー削除
    db.session.delete(user)
    db.session.commit()
    flash(f"ユーザー「{name}」を削除しました。名刺データはあなたの管理に移行されました。", "info")
    return redirect(url_for("admin.user_list"))


# ========== タグ管理 ==========

@admin_bp.route("/admin/tags")
@admin_required
def tag_list():
    """タグ一覧"""
    tags = Tag.query.order_by(Tag.sort_order, Tag.name).all()
    # 各タグの使用数を取得
    tag_counts = {}
    for tag in tags:
        tag_counts[tag.id] = CardTag.query.filter_by(tag_id=tag.id).count()
    return render_template("admin/tags.html", tags=tags, tag_counts=tag_counts)


@admin_bp.route("/admin/tags/new", methods=["POST"])
@admin_required
def create_tag():
    """タグ新規作成"""
    name = request.form.get("name", "").strip()
    if not name:
        flash("タグ名を入力してください。", "danger")
        return redirect(url_for("admin.tag_list"))

    if Tag.query.filter_by(name=name).first():
        flash("このタグ名は既に登録されています。", "danger")
        return redirect(url_for("admin.tag_list"))

    # 既存タグの最大sort_orderの次に配置
    max_order = db.session.query(db.func.max(Tag.sort_order)).scalar() or 0
    tag = Tag(name=name, sort_order=max_order + 1)
    db.session.add(tag)
    db.session.commit()
    flash(f"タグ「{name}」を作成しました。", "success")
    return redirect(url_for("admin.tag_list"))


@admin_bp.route("/admin/tags/<int:tag_id>/edit", methods=["POST"])
@admin_required
def edit_tag(tag_id):
    """タグ名変更"""
    tag = Tag.query.get_or_404(tag_id)
    new_name = request.form.get("name", "").strip()
    if not new_name:
        flash("タグ名を入力してください。", "danger")
        return redirect(url_for("admin.tag_list"))

    existing = Tag.query.filter_by(name=new_name).first()
    if existing and existing.id != tag.id:
        flash("このタグ名は既に使われています。", "danger")
        return redirect(url_for("admin.tag_list"))

    old_name = tag.name
    tag.name = new_name
    db.session.commit()
    flash(f"タグ「{old_name}」→「{new_name}」に変更しました。", "success")
    return redirect(url_for("admin.tag_list"))


@admin_bp.route("/admin/tags/<int:tag_id>/delete", methods=["POST"])
@admin_required
def delete_tag(tag_id):
    """タグ削除（紐付けも解除）"""
    tag = Tag.query.get_or_404(tag_id)
    name = tag.name
    # card_tagsの紐付けも削除
    CardTag.query.filter_by(tag_id=tag.id).delete()
    db.session.delete(tag)
    db.session.commit()
    flash(f"タグ「{name}」を削除しました。", "info")
    return redirect(url_for("admin.tag_list"))


@admin_bp.route("/admin/tags/reorder", methods=["POST"])
@admin_required
def reorder_tags():
    """タグの並び順を保存（Ajax）"""
    tag_ids = request.json.get("tag_ids", [])
    if not tag_ids:
        return jsonify({"error": "タグIDが指定されていません"}), 400

    for i, tag_id in enumerate(tag_ids):
        tag = Tag.query.get(tag_id)
        if tag:
            tag.sort_order = i
    db.session.commit()
    return jsonify({"ok": True})


# ========== 全会社一覧（管理者のみ、自分のみ設定も含む） ==========

@admin_bp.route("/admin/companies")
@admin_required
def company_list():
    """全会社一覧（visibility問わず・ID管理用）"""
    # 全会社＋名刺件数（visibility問わず）
    companies = (
        db.session.query(Company, func.count(Card.id).label("card_count"))
        .outerjoin(Card, Card.company_id == Company.id)
        .filter(Company.merged_into_id.is_(None))
        .group_by(Company.id)
        .order_by(Company.id.asc())
        .all()
    )
    # ID検索（オプション）
    keyword = request.args.get("q", "").strip()
    if keyword:
        companies = [
            (c, n) for c, n in companies
            if keyword in (c.name_ja or "") or keyword in (c.name_kana or "") or keyword == str(c.id)
        ]
    return render_template("admin/companies.html", companies=companies, keyword=keyword)
