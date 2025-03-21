Prebuilt image available at: https://hub.docker.com/r/kars85/alarmdecoder-webapp

🚀 AlarmDecoder Dockerization Guide (Raspberry Pi)

📘 Overview

This guide documents the process of Dockerizing the AlarmDecoder WebApp on a Raspberry Pi. It includes installation, environment setup, persistent database configuration, and troubleshooting steps based on real-world deployment.

📌 Prerequisites

Ensure the following before starting:

    ✅ Raspberry Pi OS (64-bit recommended)
    ✅ Docker installed on your Raspberry Pi
    ✅ AlarmDecoder hardware connected (e.g. AD2Pi "hat")
    ✅ Stable internet connection for package installation

🧰 Install Dependencies

sudo apt update && sudo apt install -y \
    apt-transport-https \
    ca-certificates \
    curl \
    software-properties-common \
    git \
    sudo

curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
newgrp docker

📁 Clone Required Repositories

git clone https://github.com/nutechsoftware/alarmdecoder.git ~/alarmdecoder
git clone https://github.com/nutechsoftware/alarmdecoder-webapp.git ~/alarmdecoder-webapp

🐳 Build Docker Image for alarmdecoder

cd ~/alarmdecoder
docker build -t alarmdecoder-python2 .

🐳 Build Docker Image for alarmdecoder-webapp

cd ~/alarmdecoder-webapp
docker build -t alarmdecoder-webapp .

🧪 Run the WebApp (First-Time Manual Test)

docker run --rm -it \
  -v ~/alarmdecoder-webapp-instance:/opt/alarmdecoder-webapp/instance \
  -p 5000:5000 \
  --device=/dev/ttyAMA0 \
  alarmdecoder-webapp
  
 Then open in your browser:
 
 http://<raspberry_pi_ip>:5000/

🔁 Run with Docker Compose
docker-compose.yml (place in ~/)

version: '3.8'

services:
  alarmdecoder-webapp:
    container_name: alarmdecoder-webapp
    image: alarmdecoder-webapp
    restart: unless-stopped
    ports:
      - "5000:5000"
    devices:
      - "/dev/ttyAMA0:/dev/ttyAMA0"
    environment:
      PUID: ${PUID}
      PGID: ${PGID}
      TZ: ${TZ}
    volumes:
      - ${HOME}/alarmdecoder-webapp-instance:/opt/alarmdecoder-webapp/instance
	  
.env file (also in ~/)
PUID=1000
PGID=1000
TZ=America/New_York
HOME=/home/pi

🚀 Launch the app

docker compose up -d

🔁(Optional) Use a Systemd Service to Start Docker Compose

If the above does not work reliably, create a systemd service for docker-compose:
	• sudo nano /etc/systemd/system/alarmdecoder.service
Paste this inside:
	[Unit]
	Description=AlarmDecoder WebApp Container
	Requires=docker.service
	After=docker.service
	
	[Service]
	WorkingDirectory=/home/YOURUSERNAME
	ExecStart=/usr/local/bin/docker-compose up -d
	ExecStop=/usr/local/bin/docker-compose down
	Restart=always
	User=USERNAME
	
	[Install]
	WantedBy=multi-user.target
	
📌 (Replace /home/USERNAME with your actual home directory path and User= with your pi's username.)

Then enable the service:
	sudo systemctl daemon-reload
	sudo systemctl enable alarmdecoder
	sudo systemctl start alarmdecoder
	
This ensures docker-compose up -d runs automatically on boot.

🛠️ Known Issues Solved

Issue	Resolution
db.sqlite not created	Added ensure_database() and ENV SQLALCHEMY_DATABASE_URI
Flask context errors	Wrapped db actions with app.app_context()
sqlite3 not installed	Installed in Dockerfile
File permission issues	Used chmod -R 777 on /instance
BroadcastMixin errors	Removed outdated socketio dependencies

✅ Final Outcome

    WebApp successfully Dockerized
    Database created and persisted
    Runs via Docker Compose on startup
    Accessible via http://<raspberry_pi_ip>:5000/


📈 Next Recommendations

    Upgrade codebase to Python 3 (Python 2 is EOL)
    Replace Flask dev server with Gunicorn or uWSGI
    Containerize alarmdecoder separately with Docker Compose for cleaner architecture
    Publish to Docker Hub and GitHub for wider reuse


