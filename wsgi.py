import os
import sys
import threading

# Ensure the application path is in the Python path for uninstalled use
BASE_DIR = os.path.dirname(__file__)
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

# If the AlarmDecoder package is installed in /opt, include it (for device communication libs, etc.)
ALARMDECODER_DIR = os.path.join(os.sep, "opt", "alarmdecoder")
if os.path.isdir(ALARMDECODER_DIR) and ALARMDECODER_DIR not in sys.path:
    sys.path.insert(0, ALARMDECODER_DIR)

from ad2web import create_app, init_app

# Create the Flask application and Socket.IO server
application, appsocket = create_app()
# Perform post-creation initialization (e.g., start decoder threads)
init_app(application, appsocket)

# Launch the Socket.IO server in a background thread (non-blocking)
socket_thread = threading.Thread(target=appsocket.serve_forever, daemon=True)
socket_thread.start()
