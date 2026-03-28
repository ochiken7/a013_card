import click
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager

db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()


def create_app():
    app = Flask(__name__)
    app.config.from_object("config.Config")

    # 拡張機能の初期化
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "ログインしてください。"
    login_manager.login_message_category = "warning"

    # モデルのimport（Alembicがテーブルを検出するため）
    from meishi.models import User, Company, Card, CardPhone, CardEmail  # noqa: F401
    from meishi.models import CardQualification, CardImage, Tag, CardTag  # noqa: F401
    from meishi.models import BatchJob, BatchItem  # noqa: F401

    # Blueprint登録
    from meishi.blueprints.auth import auth_bp
    app.register_blueprint(auth_bp)

    from meishi.blueprints.cards import cards_bp
    app.register_blueprint(cards_bp)

    from meishi.blueprints.companies import companies_bp
    app.register_blueprint(companies_bp)

    from meishi.blueprints.admin import admin_bp
    app.register_blueprint(admin_bp)

    from meishi.blueprints.csv_io import csv_bp
    app.register_blueprint(csv_bp)

    from meishi.blueprints.settings import settings_bp
    app.register_blueprint(settings_bp)

    from meishi.blueprints.batch import batch_bp
    app.register_blueprint(batch_bp)

    # 初期管理者作成コマンド
    @app.cli.command("seed-admin")
    @click.argument("email")
    @click.argument("password")
    @click.option("--name", default="管理者", help="表示名")
    def seed_admin(email, password, name):
        """初期管理者ユーザーを作成"""
        if User.query.filter_by(email=email).first():
            click.echo("このメールアドレスは既に登録されています。")
            return
        user = User(email=email, display_name=name, is_admin=True)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        click.echo(f"管理者 {email} を作成しました。")

    return app
