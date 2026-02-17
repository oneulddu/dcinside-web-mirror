import os

from flask import Flask

from .config import DevelopmentConfig, ProductionConfig
from .routes import register_routes


def create_app():
    app = Flask(__name__, instance_relative_config=True)
    env = os.getenv("MIRROR_ENV", "production").strip().lower()
    if env == "development":
        app.config.from_object(DevelopmentConfig)
    else:
        app.config.from_object(ProductionConfig)
    app.config.from_prefixed_env("MIRROR")
    register_routes(app)
    return app


app = create_app()
