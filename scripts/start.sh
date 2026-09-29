#!/usr/bin/env bash
# Abre o ClipForge. Se o servidor já estiver rodando, só abre o navegador.
cd "$(dirname "$0")/.." || exit 1
URL="http://127.0.0.1:8000"
if curl -s -o /dev/null "$URL/api/status"; then
  xdg-open "$URL" >/dev/null 2>&1 &
  exit 0
fi
exec .venv/bin/python -m app
