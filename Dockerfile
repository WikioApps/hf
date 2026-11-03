FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000 \
    PATH="/opt/hf/app/.venv/bin:/opt/hf/app/tools:${PATH}"

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libstdc++6 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 --shell /bin/sh hfbot \
    && mkdir -p /opt/hf/app /opt/hf/hf-data \
    && chown -R hfbot:hfbot /opt/hf

WORKDIR /opt/hf/app

COPY --chown=hfbot:hfbot requirements.txt app.py build.sh start.sh ./
COPY --chown=hfbot:hfbot bot/ ./bot/
COPY --chown=hfbot:hfbot web/ ./web/
COPY --chown=hfbot:hfbot scripts/ ./scripts/

USER hfbot
RUN sh build.sh --build

EXPOSE 8000
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.getenv('PORT','8000')+'/healthz',timeout=4)" || exit 1

CMD ["sh", "start.sh"]
