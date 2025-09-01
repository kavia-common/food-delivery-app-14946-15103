#!/usr/bin/env bash
set -euo pipefail
export PYTHONUNBUFFERED=1
PORT="${PORT:-8106}"
HOST="${HOST:-0.0.0.0}"
exec uvicorn app.main:app --host "${HOST}" --port "${PORT}" --reload
