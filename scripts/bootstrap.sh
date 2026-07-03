#!/usr/bin/env bash
set -euo pipefail

python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
cp -n .env.example .env || true
codex-canva doctor
