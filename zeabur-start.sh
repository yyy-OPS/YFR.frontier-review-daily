#!/bin/sh
set -eu

: "${PORT:=8080}"
: "${FRONTIER_REVIEW_DATA_DIR:=/data/frontier_review}"
: "${BIBLIOCN_CORPORA_DIR:=/data/corpora}"

mkdir -p "$FRONTIER_REVIEW_DATA_DIR" "$BIBLIOCN_CORPORA_DIR" /run/nginx

echo "[FrontierReview] starting service"
echo "[FrontierReview] public port: ${PORT}"
echo "[FrontierReview] data dir: ${FRONTIER_REVIEW_DATA_DIR}"
echo "[FrontierReview] corpora dir: ${BIBLIOCN_CORPORA_DIR}"
echo "[FrontierReview] scheduler enabled: ${FRONTIER_REVIEW_ENABLE_SCHEDULER:-true}"
echo "[FrontierReview] cors origins: ${CORS_ORIGINS:-unset}"

uvicorn app.main:app --host 127.0.0.1 --port 8000 --log-level info &
API_PID="$!"

cat > /etc/nginx/sites-enabled/default <<EOF
server {
  listen ${PORT};
  server_name _;

  root /usr/share/nginx/html;
  index index.html;

  absolute_redirect off;
  port_in_redirect off;
  server_name_in_redirect off;

  client_max_body_size 50m;
  access_log /dev/stdout;
  error_log /dev/stderr warn;

  location /api/ {
    proxy_pass http://127.0.0.1:8000/;
    proxy_http_version 1.1;
    proxy_set_header Host \$host;
    proxy_set_header X-Real-IP \$remote_addr;
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \$scheme;
    proxy_set_header X-Request-ID \$request_id;
    proxy_read_timeout 900s;
    proxy_send_timeout 900s;
    proxy_connect_timeout 30s;
  }

  location = / {
    return 302 /daily-review/;
  }

  location / {
    try_files \$uri \$uri/ /index.html;
  }
}
EOF

nginx -g "daemon off;" &
NGINX_PID="$!"

shutdown() {
  echo "[FrontierReview] shutting down"
  kill "$API_PID" "$NGINX_PID" 2>/dev/null || true
}
trap shutdown INT TERM

while true; do
  if ! kill -0 "$API_PID" 2>/dev/null; then
    echo "[FrontierReview] FastAPI process exited; stopping container"
    kill "$NGINX_PID" 2>/dev/null || true
    wait "$API_PID" 2>/dev/null || true
    exit 1
  fi
  if ! kill -0 "$NGINX_PID" 2>/dev/null; then
    echo "[FrontierReview] Nginx process exited; stopping container"
    kill "$API_PID" 2>/dev/null || true
    wait "$NGINX_PID" 2>/dev/null || true
    exit 1
  fi
  sleep 2
done
