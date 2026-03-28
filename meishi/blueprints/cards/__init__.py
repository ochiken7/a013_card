from flask import Blueprint

cards_bp = Blueprint("cards", __name__)

from meishi.blueprints.cards import routes  # noqa: E402, F401
