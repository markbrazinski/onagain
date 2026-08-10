# OnAgain — FastAPI seller + buyer app for AWS App Runner
FROM python:3.12-slim

WORKDIR /app

# system libs Pillow needs at runtime
RUN apt-get update && apt-get install -y --no-install-recommends libjpeg62-turbo zlib1g \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# app code + static UI + mannequin bases (REPO_ROOT = /app)
COPY src ./src
COPY web ./web
COPY assets ./assets

# App Runner health-checks and routes to $PORT (default 8080)
ENV PORT=8080
EXPOSE 8080
CMD ["sh", "-c", "uvicorn src.api:app --host 0.0.0.0 --port ${PORT}"]
