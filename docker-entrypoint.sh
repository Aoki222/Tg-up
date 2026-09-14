#!/bin/sh
set -e
cd /app
mkdir -p /app/download /app/sessions /app/data /app/page /app/uploaded /app/logs
exec uv run --no-dev python -m src.main
