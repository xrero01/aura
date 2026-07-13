# Aura server — cloud image (includes ffmpeg so it can hear you)
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY server/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server/ ./server/
COPY client/ ./client/

ENV AURA_PROVIDER=openrouter
# Render/most hosts inject $PORT; default to 8000 locally.
ENV PORT=8000
WORKDIR /app/server
CMD ["sh", "-c", "python -m uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
