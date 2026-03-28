"""ユーザー設定（パスワード変更・表示名変更）"""

from flask import render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from meishi import db
from meishi.blueprints.settings import settings_bp


@settings_bp.route("/settings")
@login_required
def index():
    """設定画面"""
    return render_template("settings/index.html")


@settings_bp.route("/settings/password", methods=["POST"])
@login_required
def change_password():
    """パスワード変更"""
    current_password = request.form.get("current_password", "")
    new_password = request.form.get("new_password", "")
    confirm_password = request.form.get("confirm_password", "")

    if not current_user.check_password(current_password):
        flash("現在のパスワードが正しくありません。", "danger")
        return redirect(url_for("settings.index"))

    if not new_password or len(new_password) < 4:
        flash("新しいパスワードは4文字以上にしてください。", "danger")
        return redirect(url_for("settings.index"))

    if new_password != confirm_password:
        flash("新しいパスワードが一致しません。", "danger")
        return redirect(url_for("settings.index"))

    current_user.set_password(new_password)
    db.session.commit()
    flash("パスワードを変更しました。", "success")
    return redirect(url_for("settings.index"))


@settings_bp.route("/settings/profile", methods=["POST"])
@login_required
def update_profile():
    """表示名変更"""
    display_name = request.form.get("display_name", "").strip()
    if not display_name:
        flash("表示名を入力してください。", "danger")
        return redirect(url_for("settings.index"))

    current_user.display_name = display_name
    db.session.commit()
    flash("表示名を変更しました。", "success")
    return redirect(url_for("settings.index"))
