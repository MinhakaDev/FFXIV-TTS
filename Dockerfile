FROM python:3.12-slim
WORKDIR /app

RUN apt-get update && apt-get install -y \
    portaudio19-dev \
    libsndfile1 \
    pulseaudio \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py settings.py settings.json ./
COPY lexicons ./lexicons

CMD ["python", "main.py"]