#!/usr/bin/env bash
set -euo pipefail

# Build a Lambda-compatible bundle with Linux x86_64 wheels and Python 3.13.
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

command -v uv >/dev/null || { echo "uv is required" >&2; exit 1; }
command -v zip >/dev/null || { echo "zip is required" >&2; exit 1; }

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

uv pip install --target "$tmp_dir" \
  --python-platform x86_64-manylinux2014 \
  --python-version 3.13 \
  -r requirements.txt
cp -R app "$tmp_dir/app"
cp -R static "$tmp_dir/static"
(cd "$tmp_dir" && zip -qr "$repo_root/bundle.zip" . -x '*/__pycache__/*')

echo "Created $repo_root/bundle.zip"
