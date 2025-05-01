import os
import sys
import click
import logging
from werkzeug.serving import run_with_reloader

from ad2web import create_app, init_app
from ad2web.extensions import db

# Create the Flask app and Socket.IO server
app, appsocket = None, None

@click.group()
@click.option('-c', '--config', 'config_path', required=False, help="Configuration object or file to use")
@click.pass_context
def cli(ctx, config_path):
    """Management script for the AlarmDecoder webapp."""
    global app, appsocket
    # Create the Flask application and Socket.IO server using the factory
    app, appsocket = create_app(config_path if config_path else None)
    ctx.obj = {'app': app, 'appsocket': appsocket}

@cli.command('run')
@click.pass_context
def run_server(ctx):
    """Run the development server with auto-reload."""
    app = ctx.obj['app']
    appsocket = ctx.obj['appsocket']
    app.debug = True  # Enable Flask debug mode
    logging.getLogger().setLevel(logging.DEBUG)  # Ensure debug logs are shown

    def _run_server():
        try:
            # Perform any post-app initialization (starting background threads, etc.)
            init_app(app, appsocket)
            # Start the Socket.IO (gevent) server to serve the app
            appsocket.serve_forever()
        except Exception as e:
            app.logger.error("Error running development server", exc_info=True)

    # Use Werkzeug's reloader to auto-restart on code changes
    run_with_reloader(_run_server)

@cli.command('initdb')
@click.pass_context
def initdb(ctx):
    """Initialize or reset the database (drops all tables and recreates)."""
    app = ctx.obj['app']
    # Ensure app context for database operations
    with app.app_context():
        try:
            db.drop_all()
            db.create_all()
            # Stamp the database with the latest Alembic revision
            from alembic.config import Config
            from alembic import command
            alembic_cfg = Config('alembic.ini')
            command.stamp(alembic_cfg, "head")
            # Load default notification messages
            from ad2web.notifications.models import NotificationMessage
            from ad2web.notifications.constants import DEFAULT_EVENT_MESSAGES
            for event, message in DEFAULT_EVENT_MESSAGES.items():
                db.session.add(NotificationMessage(id=event, text=message))
            db.session.commit()
        except Exception as e:
            click.echo(f"Database initialization failed: {e}", err=True)
        else:
            click.echo("Database initialization complete!")

if __name__ == "__main__":
    cli()  # Invoke the CLI
