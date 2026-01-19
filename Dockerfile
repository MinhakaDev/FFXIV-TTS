FROM python:3.12-slim

WORKDIR /app

# Install system dependencies needed by sounddevice / audio
RUN apt-get update && apt-get install -y \
    portaudio19-dev \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App files
COPY main.py settings.py settings.json ./
COPY lexicons ./lexicons

CMD ["python", "main.py"]

