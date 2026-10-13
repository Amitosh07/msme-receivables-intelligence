# API, worker and migration image. Build from the repository root:
#   docker build -f backend/Dockerfile .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/constraints.txt backend/
RUN pip install -r backend/constraints.txt \
    && pip check \
    && python -c "import xgboost, sklearn, fastapi, uvloop"

COPY alembic.ini ./
COPY backend/ backend/

RUN test -f backend/ml/models/payment_classifier_v1.json \
    && test -f backend/ml/models/payment_timing_v1.json \
    && test -f backend/ml/models/classifier_encoder_v1.joblib \
    && test -f backend/ml/models/timing_encoder_v1.joblib \
    || (echo "Model artifacts are missing from backend/ml/models; train the models before building." >&2 && exit 1)

RUN useradd --system --uid 10001 --home-dir /app app \
    && mkdir -p /data/storage \
    && chown app /data/storage

USER app
ENV ENVIRONMENT=production \
    STORAGE_ROOT=/data/storage
EXPOSE 8000

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--proxy-headers", "--forwarded-allow-ips", "*"]
