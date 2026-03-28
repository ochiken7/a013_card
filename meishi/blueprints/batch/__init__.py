from flask import Blueprint

batch_bp = Blueprint("batch", __name__)

from meishi.blueprints.batch import routes  # noqa: E402, F401
