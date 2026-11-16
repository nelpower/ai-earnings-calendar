import datetime as dt

import pytest

from src.source_health import record_status, reports, reset_health, verification_date


def test_registry_is_per_partition_and_replaces_a_partition_atomically():
    reset_health(dt.date(2026, 11, 16))
    record_status("nasdaq_ipo", "2026-11", "ok", fetched=3, accepted=1, rejected=2)
    record_status("nasdaq_ipo", "2026-12", "empty")
    record_status("earnings", "NVDA", "failed", detail="timeout")
    assert len(reports()) == 3
    record_status("nasdaq_ipo", "2026-11", "failed", detail="retry failed")
    by_key = {(r["source"], r["partition"]): r for r in reports()}
    assert len(by_key) == 3
    assert by_key[("nasdaq_ipo", "2026-12")]["last_success"] == "2026-11-16"
    assert by_key[("nasdaq_ipo", "2026-11")]["last_success"] == ""
    assert by_key[("earnings", "NVDA")]["checked_on"] == "2026-11-16"


def test_reports_are_copies_and_reset_drops_previous_run_state():
    reset_health(dt.date(2026, 11, 16))
    record_status("earnings", "NVDA", "ok", fetched=1, accepted=1)
    reports()[0]["status"] = "failed"
    assert reports()[0]["status"] == "ok"
    reset_health(dt.date(2026, 11, 17))
    assert reports() == [] and verification_date() == "2026-11-17"


def test_rejects_invalid_status_and_negative_counts():
    reset_health(dt.date(2026, 11, 16))
    with pytest.raises(ValueError):
        record_status("earnings", "NVDA", "unknown")
    with pytest.raises(ValueError):
        record_status("earnings", "NVDA", "ok", fetched=-1)
