#!/usr/bin/env sh
set -eu

cd /app/backend
exec uvicorn app.main:app --host "${APP_HOST:-0.0.0.0}" --port "${APP_PORT:-8000}"

