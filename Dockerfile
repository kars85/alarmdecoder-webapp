# Use Python 2.7 for ARM (Raspberry Pi 32-bit)
FROM arm32v7/python:2.7-slim

# Set the SQLAlchemy Database URI as an environment variable
ENV SQLALCHEMY_DATABASE_URI=sqlite:////opt/alarmdecoder-webapp/instance/app.db

# Set the working directory inside the container
WORKDIR /app

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    build-essential \
    python-dev \
	python-sh \
    python-setuptools \
    python-serial \
    libffi-dev \
    libssl-dev \
	libjpeg-dev \
    libpng-dev \
    zlib1g-dev \
	sudo \
	sqlite3 \
    libfreetype6-dev \
    git \
	nano \
    && rm -rf /var/lib/apt/lists/*

# Copy only necessary files first (to leverage Docker caching)
COPY requirements.txt /app/

# Install dependencies using pinned versions
RUN python -m pip install --upgrade "pip<21" && \
pip install --no-cache-dir -r requirements.txt

# Copy the local AlarmDecoder package from the previously built `alarmdecoder` container
COPY --from=alarmdecoder-python2 /app /app/alarmdecoder

# Install the locally built `alarmdecoder` package inside `alarmdecoder-webapp`
RUN pip install /app/alarmdecoder

# Copy the rest of the application
COPY . /app

# Ensure the required directory exists
RUN mkdir -p /opt/alarmdecoder-webapp/instance && chmod -R 777 /opt/alarmdecoder-webapp/instance

# Expose the web application port
EXPOSE 5000

# Start the web app using Flask's built-in server
CMD ["python", "manage.py", "runserver", "--host=0.0.0.0", "--port=5000"]
