"""Opzioni dell'add-on (scritte dal Supervisor in /data/options.json)."""
from __future__ import annotations

import json
import os

OPTIONS_PATH = os.environ.get("BAR_OPTIONS", "/data/options.json")
PUBLIC_PORT = int(os.environ.get("BAR_PUBLIC_PORT", "8080"))
VERSION = os.environ.get("BAR_VERSION", "dev")

OPTIONS: dict = {}


def load() -> dict:
    global OPTIONS
    try:
        with open(OPTIONS_PATH, encoding="utf-8") as f:
            OPTIONS = json.load(f)
    except FileNotFoundError:
        OPTIONS = {}
    return OPTIONS


load()
