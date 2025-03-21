🚀 AlarmDecoder Dockerization Guide (Raspberry Pi)
Up-to-Date Documentation (With Fixes)

This guide documents the steps to Dockerize AlarmDecoder on a Raspberry Pi, including installation, environment setup, database handling, and troubleshooting. It has been updated based on the challenges encountered and resolved during the process.
📌 Prerequisites

Before proceeding, ensure you have:

    ✅ Raspberry Pi OS (64-bit recommended)
    ✅ Docker installed on Raspberry Pi
    ✅ AlarmDecoder hardware connected (AD2Pi "hat")
    ✅ A stable internet connection for package installations

📌 Install Required Dependencies

Ensure your Raspberry Pi has the necessary system packages installed for Docker and AlarmDecoder.

    Update and install dependencies
        Run sudo apt update && sudo apt install -y apt-transport-https ca-certificates curl software-properties-common git sudo
    Install Docker
        Run curl -fsSL https://get.docker.com | sh
        Add your user to the Docker group: sudo usermod -aG docker $USER
    Restart your terminal session or run
        newgrp docker

📌 Clone AlarmDecoder Repositories

Clone the two required repositories for AlarmDecoder:

    git clone https://github.com/nutechsoftware/alarmdecoder.git ~/alarmdecoder
    git clone https://github.com/nutechsoftware/alarmdecoder-webapp.git ~/alarmdecoder-webapp

📌 Dockerfile for AlarmDecoder (Python 2.7)

Since AlarmDecoder relies on Python 2.7, we need to manually configure an ARM-compatible Docker image.
Create a Dockerfile in ~/alarmdecoder/

    Uses Python 2.7 for ARM (Raspberry Pi 32-bit)
    Installs system dependencies like libffi-dev, sqlite3
    Copies and installs AlarmDecoder

Build the Docker image:

    Run cd ~/alarmdecoder
    Run docker build -t alarmdecoder-python2 .

📌 Dockerfile for AlarmDecoder-WebApp

Since the web app depends on AlarmDecoder, we need to include it in the image.
Create a Dockerfile in ~/alarmdecoder-webapp/

    Uses Python 2.7 for ARM
    Ensures SQLite database is correctly created
    Installs dependencies and AlarmDecoder
    Exposes port 5000 for the web app

Build the web app Docker image:

    Run cd ~/alarmdecoder-webapp
    Run docker build -t alarmdecoder-webapp .

📌 Deploying with Docker Compose

Instead of running Docker manually, we use Docker Compose to ensure automatic startup and persistent settings.
Create a docker-compose.yml file in ~/

    Ensure it contains the correct volume mappings and environment variables.

Run the application using Docker Compose:

    Run cd ~
    Run docker compose up -d

Verify that it’s running:

    Run docker ps

Test in a browser:

    Go to http://<raspberry_pi_ip>:5000/

⚠️ Database Troubleshooting Notes

At this stage, we had database initialization issues. Below is a record of the commands that did NOT work so we can avoid retrying them in the future.
❌ Commands That Did NOT Work

    Running python manage.py initdb without SQLite installed
        The command ran but did not create db.sqlite
        Installing sqlite3 fixed this

    Running initdb multiple times manually
        The command completed, but no database was created

    Setting SQLALCHEMY_DATABASE_URI manually at runtime
        Running docker run --rm -it -e SQLALCHEMY_DATABASE_URI=sqlite:////opt/alarmdecoder-webapp/instance/db.sqlite alarmdecoder-webapp python manage.py initdb
        ❌ Did not create db.sqlite

    Checking if db.sqlite existed in the container
        Running docker run --rm -it alarmdecoder-webapp ls -lah /opt/alarmdecoder-webapp/instance
        ❌ The database file was missing

    Manually creating db.sqlite inside the container
        Running docker run --rm -it alarmdecoder-webapp sqlite3 /opt/alarmdecoder-webapp/instance/db.sqlite "CREATE TABLE test (id INTEGER PRIMARY KEY);"
        ❌ This initially failed because sqlite3 was missing
        ✅ Installing sqlite3 resolved this

    Checking SQLALCHEMY_DATABASE_URI inside the container
        Running docker run --rm -it alarmdecoder-webapp python -c "import os; print(os.environ.get('SQLALCHEMY_DATABASE_URI'))"
        ❌ Returned None, meaning it wasn't set
        ✅ Adding ENV SQLALCHEMY_DATABASE_URI in Dockerfile fixed this

📌 Final Outcome

✅ The database (db.sqlite) is now correctly created and persists
✅ Web app runs successfully at http://<raspberry_pi_ip>:5000/
✅ Database no longer disappears after restarting the container
📌 Next Steps

    Upgrade to Python 3 (Python 2 is deprecated)
    Use a Production-Ready WSGI Server (like Gunicorn instead of Flask’s development server)
    Consider Docker Compose for managing the web app and database separately