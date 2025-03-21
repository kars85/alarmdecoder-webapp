# -*- coding: utf-8 -*-

import os
import werkzeug.serving
from werkzeug.debug import DebuggedApplication
from flask_script import Manager, Command
from ad2web import create_app, init_app
from ad2web.extensions import db

import logging

# ✅ Initialize app once, globally
app, _ = create_app()

def ensure_database():
    """Ensure the database is created before starting the app."""
    db_path = "/opt/alarmdecoder-webapp/instance/app.db"

    with app.app_context():
        if not os.path.exists(db_path):
            print("Initializing database...")
            db.create_all()
            print("Database initialized!")

class RunCommand(Command):
    def run(self):
        """Run in local machine."""
        ensure_database()
        @werkzeug.serving.run_with_reloader
        def runDebugServer():
            try:
                init_app(app, appsocket)
                app.debug = True
                dapp = DebuggedApplication(app, evalex=True)
                app.decoder.appsocket.serve_forever()
            except Exception as err:
                app.logger.error("Error", exc_info=True)
        try:
            runDebugServer()
        except KeyboardInterrupt:
            pass

class InitDBCommand(Command):
    def run(self, reset=False):
        """Init/reset database."""
        try:
            with app.app_context():
                if reset:
                    print("Dropping existing database...")
                    db.drop_all()
                db.create_all()
                print("Database initialized!")
        except Exception as err:
            print("Database initialization failed: {0}".format(err))

def create_app_instance(*args, **kwargs):
    return app

manager = Manager(create_app_instance)
manager.add_command('run', RunCommand())
manager.add_command('initdb', InitDBCommand())
manager.add_option('-c', '--config',
                   dest="config",
                   required=False,
                   help="config file")

if __name__ == "__main__":
    ensure_database()
    manager.run()
