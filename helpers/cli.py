"""Custom terminal commands:  flask --app app <command>"""

import click
from werkzeug.security import generate_password_hash

from apps.core.services import user_service


@click.command("create-admin")
@click.option("--name", prompt="Full name")
@click.option("--email", prompt="Email")
@click.password_option(prompt="Password")
def create_admin_command(name, email, password):
    """Create a platform admin who can log in."""
    email = email.strip().lower()
    if user_service.get_user_by_email(email):
        raise click.ClickException(f"A user with email {email} already exists.")
    if len(password) < 8:
        raise click.ClickException("Password must be at least 8 characters.")
    user_service.create_user(name.strip(), email, generate_password_hash(password))
    click.echo(f"Admin {email} created. You can now log in.")


def init_app(app):
    app.cli.add_command(create_admin_command)
