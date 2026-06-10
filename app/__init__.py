import os

from flask import Flask, request
from flask_compress import Compress

from env_loader import load_dotenv

load_dotenv()

from .config import DevelopmentConfig, ProductionConfig
from .routes import register_routes
from .services.highlight import highlight_search_term

compress = Compress()


def _init_response_compression(app):
    app.config.setdefault("COMPRESS_REGISTER", False)
    app.config.setdefault("COMPRESS_MIMETYPES", ["text/html", "application/json"])
    compress.init_app(app)

    @app.after_request
    def compress_response(response):
        if request.endpoint in {"main.media", "main.movie"}:
            return response
        return compress.after_request(response)


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
    _init_response_compression(app)
    return app


app = create_app()
