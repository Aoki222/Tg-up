# --- 前端 ---
FROM node:22-alpine AS frontend
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- 运行镜像 ---
FROM python:3.12-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.6.14 /uv /usr/local/bin/uv

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src ./src
COPY upload.toml.example docker-entrypoint.sh ./
COPY --from=frontend /web/dist ./frontend/dist

RUN chmod +x /app/docker-entrypoint.sh \
    && uv sync --frozen --no-dev

ENV PYTHONUNBUFFERED=1 \
    API_HOST=0.0.0.0 \
    API_PORT=8000 \
    PATH="/app/.venv/bin:$PATH"

EXPOSE 8000
ENTRYPOINT ["/app/docker-entrypoint.sh"]
