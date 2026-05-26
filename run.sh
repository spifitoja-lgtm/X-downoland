#!/usr/bin/env bash
cd "$(dirname "$0")"
exec uv run --quiet x_scraper.py "$@"
