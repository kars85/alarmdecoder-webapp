#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import sys

# Fix sys path if running from source.
if __package__ is None and os.path.dirname(os.path.dirname(__file__)) not in sys.path:
    sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from flask_script import Manager, Command, Option
from ad2web import create_app, init_app
from ad2web.commands import RunCommand, InitDBCommand # Import the commands

app, appsocket = None, None

def _create_app(**kwargs):
    global app, appsocket

    app, appsocket = create_app() # Calls the function from ad2web/app.py

    return app # Returns only the app object

# ... Commands ...

manager = Manager(_create_app) # Manager is instantiated HERE using the factory function
manager.add_command('run', RunCommand())
manager.add_command('initdb', InitDBCommand())
# ... options ...

if __name__ == "__main__":
    manager.run()
