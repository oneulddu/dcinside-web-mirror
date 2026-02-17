from app import app


if __name__ == "__main__":
    app.run(
        debug=app.config.get("DEBUG", False),
        host="0.0.0.0",
        port=8080,
    )
