FROM python:3.11-slim

# Install system dependencies (exiftool for telemetry)
RUN apt-get update && apt-get install -y exiftool libpq-dev gcc && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 3080