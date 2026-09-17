# syntax=docker/dockerfile:1.4

FROM python:3.10-slim-bookworm

ARG PIP_INDEX_URL=https://pypi.org/simple
ARG PRELOAD_MODELS=true

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MODELSCOPE_CACHE=/models/modelscope \
    HF_HOME=/models/huggingface \
    FUNASR_PRELOAD_MODELS=offline,streaming \
    FUNASR_PRELOAD_STRICT=true

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        libgomp1 \
        libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 app \
    && mkdir -p /models /model-cache /data \
    && chown -R app:app /app /models /model-cache /data

COPY --chown=app:app pyproject.toml ./
COPY --chown=app:app backend ./backend

RUN pip install --no-cache-dir --index-url "${PIP_INDEX_URL}" .

USER app

# Build the model cache once and copy it into the image. BuildKit keeps the
# download cache between builds, so source changes do not re-download models.
RUN --mount=type=cache,target=/model-cache,uid=1000,gid=1000 \
    if [ "${PRELOAD_MODELS}" = "true" ]; then \
        python -m backend.scripts.preload_models \
        && mkdir -p /models \
        && cp -a /model-cache/modelscope /models/; \
    fi

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=600s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=3)"

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--ws-ping-interval", "20", "--ws-ping-timeout", "30"]
