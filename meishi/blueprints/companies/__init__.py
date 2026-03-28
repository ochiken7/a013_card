from flask import Blueprint

companies_bp = Blueprint("companies", __name__)

from meishi.blueprints.companies import routes  # noqa: E402, F401
