FROM node:20-alpine AS web-build
WORKDIR /web
RUN corepack enable && corepack prepare pnpm@9.15.9 --activate
COPY apps/web/package.json apps/web/pnpm-lock.yaml* ./
RUN pnpm install --no-frozen-lockfile
COPY apps/web/ ./
RUN rm -f pnpm-workspace.yaml
ARG VITE_API_BASE=/api
ENV VITE_API_BASE=$VITE_API_BASE
RUN pnpm run build

FROM python:3.12-slim

ARG PIP_INDEX_URL=
ARG PIP_TRUSTED_HOST=

WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends nginx fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

COPY services/agent/requirements.txt .
RUN set -eux; \
    EXTRA=""; \
    if [ -n "$PIP_INDEX_URL" ]; then EXTRA="--index-url $PIP_INDEX_URL"; fi; \
    if [ -n "$PIP_TRUSTED_HOST" ]; then EXTRA="$EXTRA --trusted-host $PIP_TRUSTED_HOST"; fi; \
    pip install --no-cache-dir $EXTRA -r requirements.txt

COPY services/agent/app/ app/
COPY --from=web-build /web/dist /usr/share/nginx/html
COPY zeabur-start.sh /usr/local/bin/zeabur-start.sh

RUN chmod +x /usr/local/bin/zeabur-start.sh \
    && mkdir -p /data/frontier_review /data/corpora /run/nginx

ENV PORT=8080
ENV FRONTIER_REVIEW_DATA_DIR=/data/frontier_review
ENV BIBLIOCN_CORPORA_DIR=/data/corpora
ENV FRONTIER_REVIEW_REQUIRE_SECURE_CONFIG=true

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD ["python", "-c", "import os,sys,urllib.request; p=os.environ.get('PORT','8080'); sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{p}/api/healthz',timeout=4).status==200 else 1)"]

CMD ["/usr/local/bin/zeabur-start.sh"]
