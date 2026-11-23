from flask import Flask, jsonify, render_template
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from apps.admin.routes import admin_bp
from config import Config
from helpers import cli, database, logger, security


def create_app(config_overrides=None):
    app = Flask(__name__)
    app.config.from_object(Config)
    if config_overrides:
        app.config.update(config_overrides)

    if not app.config.get("SECRET_KEY"):
        raise RuntimeError(
            "SECRET_KEY is not set. In production it is required: add a long random value to .env, e.g.\n"
            "  python -c \"import secrets; print('SECRET_KEY=' + secrets.token_hex(32))\" >> .env"
        )

    logger.init_app(app)
    if app.config.get("SECRET_KEY_IS_TEMPORARY"):
        app.logger.warning("SECRET_KEY is not set: using a temporary key, so logins reset on every restart.")
    if app.config.get("TRUST_PROXY"):
        # Behind nginx / a load balancer: trust X-Forwarded-For/Proto for the real client IP and https.
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    database.init_app(app)
    security.init_app(app)
    cli.init_app(app)
    app.register_blueprint(admin_bp)
    _register_error_handlers(app)
    return app


def _register_error_handlers(app):
    """Friendly pages (or JSON for API calls) instead of raw errors or tracebacks."""

    @app.errorhandler(HTTPException)
    def http_error(exc):
        if security.wants_json():
            return jsonify(error=exc.description or exc.name), exc.code
        return render_template("error.html", code=exc.code, title=exc.name, message=exc.description), exc.code

    @app.errorhandler(Exception)
    def unexpected_error(exc):
        app.logger.exception("Unhandled error")
        if security.wants_json():
            return jsonify(error="Something went wrong on the server. Please try again."), 500
        return render_template(
            "error.html", code=500, title="Something went wrong",
            message="An unexpected error happened. It has been logged; please try again.",
        ), 500


app = create_app()

if __name__ == "__main__":
    # Development server only. In production use gunicorn (see README.md).
    # use_debugger=False: keep auto-reload, but turn off Werkzeug's in-browser debugger.
    # That debugger can run Python code, which is dangerous if the app is shared
    # (e.g. through ngrok). Errors are still printed in the terminal.
    app.run(debug=app.config["DEBUG"], use_debugger=False)
