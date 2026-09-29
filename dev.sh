#!/usr/bin/env bash
set -e

# 确保在项目根目录下执行
cd "$(dirname "$0")"

export WATCHFILES_MANAGED="1"
uv run watchfiles "uv run python -m src.main" src/
