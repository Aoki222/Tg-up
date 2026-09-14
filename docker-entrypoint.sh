#!/bin/sh
set -e
cd /app
mkdir -p /app/download /app/sessions /app/data /app/page /app/uploaded /app/logs
if [ -d /app/upload.toml ]; then
  echo "upload.toml 被挂成了目录。请在宿主机执行: cp upload.toml.example upload.toml"
  exit 1
fi
if [ ! -f /app/upload.toml ]; then
  cp /app/upload.toml.example /app/upload.toml
fi
exec uv run --no-dev python -m src.main
