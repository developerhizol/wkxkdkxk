FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEBIAN_FRONTEND=noninteractive

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

COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install -r requirements.txt

COPY . .

# <<< ВОТ ЭТА СТРОКА БЫЛА ПОТЕРЯНА В ПРОШЛЫЙ РАЗ >>>
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

RUN test -f /app/static/index.html || (echo "FATAL: static/index.html не найден!" && exit 1) && \
    echo "OK: static/index.html присутствует" && \
    mkdir -p data/uploads/svg data/uploads/fonts

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:${PORT:-4263}/health || exit 1

EXPOSE 4263

CMD ["supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
