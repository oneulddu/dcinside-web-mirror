import os

from flask import Flask, request, url_for
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


def _init_static_cache_busting(app):
    def static_url(filename):
        path = os.path.join(app.static_folder, filename)
        try:
            version = str(int(os.path.getmtime(path)))
        except OSError:
            return url_for("static", filename=filename)
        return url_for("static", filename=filename, v=version)

    app.jinja_env.globals["static_url"] = static_url

    @app.after_request
    def add_static_cache_headers(response):
        if request.endpoint == "static":
            response.cache_control.public = True
            response.cache_control.max_age = 31536000
            response.cache_control.immutable = True
        return response


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
    _init_static_cache_busting(app)
    _init_response_compression(app)
    return app


app = create_app()
