FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    BASE_DOWNLOAD_DIR=/data/downloads \
    METRICS_DB_PATH=/data/state/metrics.sqlite3 \
    MEDIA_CACHE_DB_PATH=/data/state/media_cache.db \
    FIREFOX_PROFILE_PATH=/data/cookies/firefox \
    IG_SESSION_FILE=/data/state/ig_session.json

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates ffmpeg nodejs \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install --with-deps chromium

RUN groupadd --gid 10001 mediaraven \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /app mediaraven \
    && mkdir -p /data/downloads /data/state /data/cookies \
    && chown -R mediaraven:mediaraven /data /app

COPY --chown=mediaraven:mediaraven . /app/

USER 10001:10001
EXPOSE 8282
HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8282/live', timeout=5).close()" || exit 1

CMD ["python", "mediaraven.py"]
