#!/bin/bash
# Aethelark — integrated web-rendered app (native pill + QWebEngine dashboard + real backend).
# This is the only entry point. The QPainter cockpit was deleted on
# 2026-08-28; main.py is a library now and running it does nothing.
cd "$(dirname "$0")"
source .venv/bin/activate
exec python aethelark_web.py "$@"
