import os

from app import create_app
from env_loader import env_int


def main():
    app = create_app()
    host = os.getenv("MIRROR_HOST", "0.0.0.0")
    port = env_int("MIRROR_PORT", 8080, minimum=1, maximum=65535)
    app.run(
        debug=app.config.get("DEBUG", False),
        host=host,
        port=port,
    )


if __name__ == "__main__":
    main()
