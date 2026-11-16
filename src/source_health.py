"""Per-run source diagnostics, independent of event generation and persistence.

Providers keep their list-returning APIs. The pipeline resets this registry for
each run, then uses partition failures to retain only affected previous data.
Historical ``last_success`` values are inherited by the persistence layer.
"""
from __future__ import annotations

import datetime as dt

from src import config

_STATUSES = {"ok", "empty", "failed", "invalid_schema"}
_checked_on: str | None = None
_reports: dict[tuple[str, str], dict] = {}


def reset_health(today: dt.date) -> None:
    global _checked_on
    _checked_on = today.isoformat()[:10]
    _reports.clear()


def verification_date() -> str:
    """Data-day used by both diagnostics and newly verified event metadata."""
    return _checked_on or config.today_local().isoformat()


def record_status(source: str, partition: str, status: str, fetched: int = 0,
                  accepted: int = 0, detail: str = "", rejected: int = 0) -> None:
    if status not in _STATUSES:
        raise ValueError(f"Unknown source status: {status}")
    if min(fetched, accepted, rejected) < 0:
        raise ValueError("Source counts must be non-negative")
    checked_on = verification_date()
    _reports[(source, partition)] = {
        "source": source, "partition": partition, "status": status,
        "fetched": fetched, "accepted": accepted, "rejected": rejected,
        "detail": str(detail), "checked_on": checked_on,
        "last_success": checked_on if status in {"ok", "empty"} else "",
    }


def reports() -> list[dict]:
    """Return stable, independent copies; callers may enrich them for storage."""
    return [dict(_reports[key]) for key in sorted(_reports)]
