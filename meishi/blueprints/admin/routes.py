"""ユーザー管理（管理者のみ）"""

from functools import wraps
from flask import render_template, redirect, url_for, flash, request, abort
from flask_login import login_required, current_user
from meishi import db
from meishi.blueprints.admin import admin_bp
from meishi.models.user import User


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
