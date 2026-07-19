"""Local persistence for ASR engine benchmark runs: JSON array in
%APPDATA%\\JoyVoice\\benchmarks.json. Each run records what every engine
produced for one test clip and which one the user marked best."""

from __future__ import annotations

import json
import logging
from typing import Any

from app.storage import paths

logger = logging.getLogger("joyvoice.benchmark_store")

MAX_RUNS = 100


def load() -> list[dict[str, Any]]:
    path = paths.data_dir() / "benchmarks.json"
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as exc:
        logger.warning("Could not read benchmarks.json: %s", exc)
        return []


def append(run: dict[str, Any]) -> None:
    runs = load()
    runs.append(run)
    if len(runs) > MAX_RUNS:
        runs = runs[-MAX_RUNS:]
    path = paths.data_dir() / "benchmarks.json"
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(runs, f, indent=2, ensure_ascii=False)
    except Exception as exc:
        logger.error("Could not save benchmarks.json: %s", exc)
