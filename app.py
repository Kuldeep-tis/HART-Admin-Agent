from flask import Flask

from apps.admin.routes import admin_bp
from config import Config
from helpers import cli, database


def create_app(config_overrides=None):
    app = Flask(__name__)
    app.config.from_object(Config)
    if config_overrides:
        app.config.update(config_overrides)

    database.init_app(app)
    cli.init_app(app)
    app.register_blueprint(admin_bp)
    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
