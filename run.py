import os

from app import create_app


def main():
    app = create_app()
    host = os.getenv("MIRROR_HOST", "0.0.0.0")
    port = int(os.getenv("MIRROR_PORT", "8080"))
    app.run(
        debug=app.config.get("DEBUG", False),
        host=host,
        port=port,
    )


if __name__ == "__main__":
    main()
