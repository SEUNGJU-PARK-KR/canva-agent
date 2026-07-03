#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "사용법: ./scripts/push_private.sh git@github.com:OWNER/REPO.git" >&2
  exit 1
fi

REMOTE="$1"

git init
git add .
git commit -m "Create Codex Canva content agent"
git branch -M main
git remote remove origin 2>/dev/null || true
git remote add origin "$REMOTE"
git push -u origin main
