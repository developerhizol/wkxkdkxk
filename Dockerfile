# ─── Base ─────────────────────────────────────────────────────────────
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEBIAN_FRONTEND=noninteractive

# ─── System deps ──────────────────────────────────────────────────────
# libgomp1        — OpenMP для некоторых сборок (rlottie)
# libfreetype6    — рендер шрифтов через Pillow
# fonts-dejavu    — базовые шрифты (fallback, если CDN недоступен)
# build-essential, cmake — нужны для сборки rlottie-python и svgelements
# libgl1, libglib2.0-0 — Pillow может тянуть графические либы
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        cmake \
        git \
        pkg-config \
        libgomp1 \
        libfreetype6 \
        libgl1 \
        libglib2.0-0 \
        fonts-dejavu-core \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ─── Python deps (кэшируем отдельно от кода) ─────────────────────────
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install -r requirements.txt

# ─── Code ─────────────────────────────────────────────────────────────
COPY . .

# Директории для данных и загрузок
RUN mkdir -p data/uploads/svg data/uploads/fonts static

# ─── Healthcheck ──────────────────────────────────────────────────────
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://127.0.0.1:${PORT:-4263}/api/templates || exit 1

# ─── Runtime ──────────────────────────────────────────────────────────
# supervisor запускает uvicorn + bot.py в одном контейнере
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

EXPOSE 4263

CMD ["supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]