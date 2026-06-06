FROM python:3.12-slim

ARG PIP_INDEX_URL=
ARG PIP_TRUSTED_HOST=

WORKDIR /app
COPY requirements.txt .
RUN set -eux; \
    EXTRA=""; \
    if [ -n "$PIP_INDEX_URL" ]; then EXTRA="--index-url $PIP_INDEX_URL"; fi; \
    if [ -n "$PIP_TRUSTED_HOST" ]; then EXTRA="$EXTRA --trusted-host $PIP_TRUSTED_HOST"; fi; \
    pip install --no-cache-dir $EXTRA -r requirements.txt

COPY app/ app/
RUN useradd -m -u 10001 appuser \
    && mkdir -p /data/frontier_review /data/corpora \
    && chown -R appuser /app /data/frontier_review /data/corpora
USER appuser

ENV PORT=8000
ENV FRONTIER_REVIEW_DATA_DIR=/data/frontier_review
ENV FRONTIER_REVIEW_REQUIRE_SECURE_CONFIG=true
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD ["python", "-c", "import os,sys,urllib.request; p=os.environ.get('PORT','8000'); sys.exit(0 if urllib.request.urlopen(f'http://localhost:{p}/healthz',timeout=4).status==200 else 1)"]

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
