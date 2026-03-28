from flask import Blueprint

csv_bp = Blueprint("csv_io", __name__)

from meishi.blueprints.csv_io import routes  # noqa: E402, F401
