import os
import re

from flask import Flask
from markupsafe import Markup, escape

from env_loader import load_dotenv

load_dotenv()

from .config import DevelopmentConfig, ProductionConfig
from .routes import register_routes


def highlight_search_term(value, keyword=None):
    text = "" if value is None else str(value)
    term = (keyword or "").strip()
    if not term:
        return escape(text)

    pattern = re.compile(re.escape(term), re.IGNORECASE)
    pieces = []
    last = 0
    for match in pattern.finditer(text):
        start, end = match.span()
        pieces.append(str(escape(text[last:start])))
        pieces.append('<mark class="search-highlight">')
        pieces.append(str(escape(text[start:end])))
        pieces.append("</mark>")
        last = end
    if not pieces:
        return escape(text)
    pieces.append(str(escape(text[last:])))
    return Markup("".join(pieces))


def create_app():
    app = Flask(__name__, instance_relative_config=True)
    env = os.getenv("MIRROR_ENV", "production").strip().lower()
    if env == "development":
        app.config.from_object(DevelopmentConfig)
    else:
        app.config.from_object(ProductionConfig)
    app.config.from_prefixed_env("MIRROR")
    app.add_template_filter(highlight_search_term, "highlight_search")
    register_routes(app)
    return app


app = create_app()
