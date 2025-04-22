# manage.py (relevant parts)
from flask_script import Manager, Command
from ad2web import create_app, init_app
# ... other imports

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
