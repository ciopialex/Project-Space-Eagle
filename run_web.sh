#!/bin/bash
# Aethelark — integrated web-rendered app (native pill + QWebEngine dashboard + real backend).
# The classic QPainter app still lives at `eagle` / main.py, untouched.
cd "$(dirname "$0")"
source .venv/bin/activate
exec python aethelark_web.py "$@"
