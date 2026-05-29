"""Paths and constants for the AI earnings calendar."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
AI_COMPANIES_PATH = DATA_DIR / "ai_companies.yaml"
OUTPUTS_DIR = BASE_DIR / "outputs"
SITE_DIR = BASE_DIR / "site"

EARNINGS_JSON = OUTPUTS_DIR / "earnings.json"

# Time windows (calendar days from "today").
WINDOW_DAYS = 7      # the headline "this week" list
PREVIEW_DAYS = 30    # a secondary "coming up" preview


def load_yaml(path: str | Path) -> Any:
    path = Path(path)
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_companies(path: str | Path = AI_COMPANIES_PATH) -> list[dict]:
    data = load_yaml(path)
    return data.get("companies", []) if isinstance(data, dict) else []
