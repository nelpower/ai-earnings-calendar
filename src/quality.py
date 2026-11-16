"""Observable source health and narrowly scoped recovery of known future events."""
from __future__ import annotations

import datetime as dt
import json
import os
from collections import Counter
from dataclasses import replace
from pathlib import Path

from src import config
from src.events_model import Event
from src.event_state import same_event

BAD_STATUSES = {"failed", "invalid_schema"}


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def source_key(event: Event) -> tuple[str, str]:
    meta = event.meta
    if meta.get("source"):
        partition = str(meta.get("source_partition", ""))
        return str(meta["source"]), partition.upper() if meta["source"] == "earnings" else partition
    # Migrate the previously committed feed without misclassifying curated IPOs.
    if event.category == "earnings":
        return "earnings", str(meta.get("ticker") or next(iter(event.tickers), "")).upper()
    if event.category == "ipo" and "nasdaq.com" in event.source_url:
        if meta.get("ipo_date") or "解禁" in event.title:
            return "nasdaq_lockup", str(meta.get("ipo_date", ""))[:7]
        return "nasdaq_ipo", event.date[:7]
    return "", ""


def recover_failed(current: list[Event], previous: list[Event], sources: list[dict],
                   today: dt.date, previous_day: str = "") -> list[Event]:
    """Reuse only partitions explicitly reported failed; successful empties clear.

    A repeated outage never advances last_verified. The display-window filter
    runs later, so expired records cannot be resurrected into the live calendar.
    """
    failed = {(r["source"], r["partition"]): r for r in sources
              if r.get("status") in BAD_STATUSES}
    out = list(current)
    for event in previous:
        key = source_key(event)
        if key not in failed or event.date < today.isoformat():
            continue
        # A current official/backstop record can supersede a cached feed record
        # even when its date or title changed; pricing and listing stay distinct.
        if any(same_event(event, fresh) for fresh in out):
            continue
        meta = dict(event.meta)
        meta.update(source=key[0], source_partition=key[1], source_stale=True)
        if not meta.get("stale_since"):
            meta["stale_since"] = today.isoformat()
        if not meta.get("last_verified"):
            meta["last_verified"] = failed[key].get("last_success") or previous_day or "未知"
        out.append(replace(event, meta=meta))
    return out


def carry_source_history(sources: list[dict], previous_report: dict) -> list[dict]:
    previous = {(r.get("source"), r.get("partition")): r
                for r in previous_report.get("sources", [])}
    out = []
    for report in sources:
        row = dict(report)
        old = previous.get((row.get("source"), row.get("partition")), {})
        if row.get("status") in BAD_STATUSES:
            row["last_success"] = old.get("last_success", "")
            row["consecutive_failures"] = old.get("consecutive_failures", 0) + 1
        else:
            row["last_success"] = row.get("checked_on", "")
            row["consecutive_failures"] = 0
        out.append(row)
    return sorted(out, key=lambda r: (r.get("source", ""), r.get("partition", "")))


def build_report(events: list[Event], previous: list[Event], sources: list[dict],
                 candidates: list[dict], static: list[Event], today: dt.date,
                 company_count: int, issuer_count: int, conflicts: list[dict]) -> dict:
    end = today + dt.timedelta(days=config.PREVIEW_DAYS)
    counts = Counter(e.category for e in events)
    previous_counts = Counter(e.category for e in previous if today.isoformat() <= e.date <= end.isoformat())
    warnings = []
    for category, count in previous_counts.items():
        if count >= 3 and counts.get(category, 0) < count / 2:
            warnings.append({"kind": "coverage_drop", "category": category,
                             "before": count, "after": counts.get(category, 0)})
    review_due = []
    for event in static:
        if not today.isoformat() <= event.date <= end.isoformat():
            continue
        checked = event.meta.get("verified_on") or event.meta.get("last_verified", "")
        try:
            age = (today - dt.date.fromisoformat(checked)).days
        except (ValueError, TypeError):
            age = None
        # A pending, near-term date needs a fresh primary-source check.
        threshold = 1 if event.meta.get("date_confirmed") is False else 30
        if age is None or age > threshold:
            review_due.append({"title": event.title, "date": event.date,
                               "last_verified": checked, "source_url": event.source_url})
    if review_due:
        warnings.append({"kind": "manual_review_due", "count": len(review_due)})
    macro_dates = [e.date for e in static if e.category in {"macro", "fed"}]
    if macro_dates and max(macro_dates)[:4] <= str(today.year) and end.year > today.year:
        warnings.append({"kind": "macro_seed_expiring", "last_date": max(macro_dates)})
    failed = [r for r in sources if r.get("status") in BAD_STATUSES]
    stale = sum(any(e.meta.get(k) for k in ("source_stale", "date_source_stale", "estimates_stale")) for e in events)
    pending = sum(c.get("review_status") == "needs_review" for c in candidates)
    return {"checked_on": today.isoformat(),
            "status": "degraded" if failed or stale or warnings or conflicts else "ok",
            "company_count": company_count, "issuer_count": issuer_count,
            "official_source_count": sum(r.get("source") == "official_discovery" for r in sources),
            "failed_partitions": len(failed), "stale_events": stale,
            "pending_candidates": pending, "event_counts": dict(sorted(counts.items())),
            "sources": sources, "warnings": warnings, "date_conflicts": conflicts,
            "manual_review_due": review_due}


def write_report(report: dict, candidates: list[dict], outputs_dir: Path, site_dir: Path):
    for directory in (outputs_dir, site_dir):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "source_status.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        (directory / "candidates.json").write_text(
            json.dumps({"checked_on": report["checked_on"], "candidates": candidates},
                       ensure_ascii=False, indent=2), encoding="utf-8")


def workflow_summary(report: dict):
    bad = [r for r in report.get("sources", []) if r.get("status") in BAD_STATUSES]
    for row in bad:
        # No exception bodies, arbitrary source text, or credentials in annotations.
        label = f'{row["source"]}/{row["partition"]}'.replace("\n", " ")
        print(f'::warning title=Calendar source unavailable::{label} ({row["status"]})')
    for warning in report.get("warnings", []):
        print(f'::warning title=Calendar coverage review::{warning["kind"]}')
    if report.get("date_conflicts"):
        print('::warning title=Calendar date conflict::Official date review is required.')
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("## Calendar collection health\n\n")
            fh.write(f'- Listed companies: {report.get("company_count", 0)}\n')
            fh.write(f'- Official discovery sources: {report.get("official_source_count", 0)}\n')
            fh.write(f'- Failed source partitions: {report.get("failed_partitions", 0)}\n')
            fh.write(f'- Cached future events: {report.get("stale_events", 0)}\n')
            fh.write(f'- Candidates awaiting review: {report.get("pending_candidates", 0)}\n')
            fh.write(f'- Date conflicts: {len(report.get("date_conflicts", []))}\n')


if __name__ == "__main__":
    report = read_json(config.OUTPUTS_DIR / "source_status.json", {})
    if report:
        workflow_summary(report)
