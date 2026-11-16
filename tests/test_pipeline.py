"""Tests for the unified event pipeline (earnings + IPO providers mocked)."""
import datetime as dt
import json

import pytest

from src import pipeline
from src.events_model import Event
from src.source_health import record_status


@pytest.fixture(autouse=True)
def offline_discovery(monkeypatch):
    monkeypatch.setattr(pipeline, "discover", lambda today, horizon, previous: ([], previous or []))


def test_run_merges_and_windows(monkeypatch, tmp_path):
    today = dt.date(2026, 5, 29)
    # mock the network providers so the test is offline & deterministic
    monkeypatch.setattr(pipeline, "earnings_events", lambda today, h, throttle=0.0: [])
    monkeypatch.setattr(pipeline, "ipo_events", lambda today, h: [])
    monkeypatch.setattr(pipeline, "lockup_events", lambda today, h: [])

    this_week, upcoming = pipeline.run(
        outputs_dir=tmp_path / "o", site_dir=tmp_path / "s", today=today)

    tw_titles = " ".join(e.title for e in this_week)
    assert "GTC" in tw_titles          # 黄仁勋 GTC Taipei 6/1 (+3d)
    assert "非农" in tw_titles          # jobs report 6/5 (+7d, edge of this week)

    up_cats = {e.category for e in upcoming}
    assert "fed" in up_cats            # FOMC 6/16-17
    assert "options" in up_cats        # quad witching 6/19
    assert "index" in up_cats          # Russell reconstitution 6/26

    # nothing beyond the horizon (July FOMC 7/28 is +60d -> excluded)
    assert all((dt.date.fromisoformat(e.date) - today).days <= pipeline.config.PREVIEW_DAYS
               for e in this_week + upcoming)
    assert (tmp_path / "s" / "index.html").exists()
    assert (tmp_path / "o" / "events.json").exists()


def test_earnings_fallback_only_when_reported_failed(monkeypatch, tmp_path):
    out = tmp_path / "o"
    out.mkdir()
    (out / "events.json").write_text(
        '{"events":[{"date":"2026-06-04","category":"earnings",'
        '"title":"Broadcom 财报","importance":3,"tickers":["AVGO"]}]}',
        encoding="utf-8")
    def failed(today, h, throttle=0.0):
        record_status("earnings", "AVGO", "failed")
        return []
    monkeypatch.setattr(pipeline, "earnings_events", failed)
    monkeypatch.setattr(pipeline, "ipo_events", lambda today, h: [])
    monkeypatch.setattr(pipeline, "lockup_events", lambda today, h: [])

    this_week, upcoming = pipeline.run(
        outputs_dir=out, site_dir=tmp_path / "s", today=dt.date(2026, 5, 29))
    all_titles = " ".join(e.title for e in this_week + upcoming)
    assert "Broadcom" in all_titles    # reused last-good earnings


def test_curated_ipo_backstop_dedupes_the_same_issuer_and_phase():
    curated = Event(date="2026-11-18", category="ipo", title="Sourced pricing",
                    tickers=["ANT"], meta={"event_type": "pricing"})
    duplicate = Event(date=curated.date, category="ipo", title="Feed pricing",
                      tickers=["ant"], meta={"event_type": "pricing"})
    listing = Event(date=curated.date, category="ipo", title="First trading day",
                    tickers=["ANT"], meta={"event_type": "listing"})
    another_date = Event(date="2026-11-19", category="ipo", title="Other date",
                         tickers=["ANT"], meta={"event_type": "pricing"})
    assert pipeline._merge_ipo_events([curated], [duplicate, listing, another_date]) == [curated, listing]


def test_actual_pricing_replaces_tentative_backstop_even_when_date_changes():
    curated = Event(date="2026-11-18", category="ipo", title="预计定价",
                    importance=3, tickers=["ANT"],
                    meta={"event_type": "pricing", "date_confirmed": False})
    actual = Event(date="2026-11-19", category="ipo", title="定价",
                   importance=1, tickers=["ANT"],
                   meta={"event_type": "pricing", "date_confirmed": True})
    merged = pipeline._merge_ipo_events([curated], [actual])
    assert len(merged) == 1 and merged[0].date == "2026-11-19"
    assert merged[0].meta["date_confirmed"] is True
    assert merged[0].title == "定价" and merged[0].importance == 3


def test_november_events_survive_an_empty_ipo_feed(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline, "earnings_events", lambda *a, **k: [])
    monkeypatch.setattr(pipeline, "ipo_events", lambda *a, **k: [])
    monkeypatch.setattr(pipeline, "lockup_events", lambda *a, **k: [])
    this_week, upcoming = pipeline.run(
        outputs_dir=tmp_path / "o", site_dir=tmp_path / "s", today=dt.date(2026, 11, 16))
    anthropic = [e for e in this_week if "Anthropic" in e.title]
    assert {(e.date, e.meta["event_type"]) for e in anthropic} == {
        ("2026-11-18", "pricing"), ("2026-11-19", "listing")}
    assert all(e.meta["date_confirmed"] is False for e in anthropic)
    assert not any("黄仁勋" in e.title for e in this_week)
    briefing = [e for e in upcoming if "黄仁勋" in e.title]
    assert len(briefing) == 1 and briefing[0].date == "2026-12-01"
    assert briefing[0].meta["date_confirmed"] is False


def test_partial_earnings_failure_keeps_only_failed_ticker(monkeypatch, tmp_path):
    today = dt.date(2026, 11, 16)
    previous = [Event(date="2026-11-18", category="earnings", title=t + " 财报", tickers=[t])
                for t in ("NVDA", "AMD", "AVGO")]
    out = tmp_path / "o"
    out.mkdir()
    (out / "events.json").write_text(json.dumps({"generated": "2026-11-15", "events": [e.to_dict() for e in previous]}), encoding="utf-8")
    def fetch(*args, **kwargs):
        record_status("earnings", "NVDA", "ok", fetched=1, accepted=1)
        record_status("earnings", "AMD", "failed")
        record_status("earnings", "AVGO", "empty", fetched=1)
        return [Event(date="2026-11-19", category="earnings", title="NVDA 财报", tickers=["NVDA"])]
    monkeypatch.setattr(pipeline, "earnings_events", fetch)
    monkeypatch.setattr(pipeline, "ipo_events", lambda *a: [])
    monkeypatch.setattr(pipeline, "lockup_events", lambda *a: [])
    a, b = pipeline.run(outputs_dir=out, site_dir=tmp_path / "s", today=today)
    earnings = {e.tickers[0]: e for e in a + b if e.category == "earnings"}
    assert set(earnings) == {"NVDA", "AMD"}
    assert earnings["NVDA"].date == "2026-11-19"
    assert earnings["AMD"].meta["source_stale"] is True
    assert earnings["AMD"].meta["last_verified"] == "2026-11-15"
    report = json.loads((out / "source_status.json").read_text(encoding="utf-8"))
    assert report["failed_partitions"] == 1 and report["stale_events"] == 1


def test_official_earnings_date_overrides_yahoo_and_preserves_estimates():
    yahoo = Event(date="2026-11-17", category="earnings", title="NVIDIA 财报", tickers=["NVDA"],
                  meta={"eps_estimate": 2.4, "revenue_estimate": 100e9, "source": "earnings"})
    official = Event(date="2026-11-18", category="earnings", title="NVIDIA Financial Results",
                     tickers=["NVDA"], source_url="https://investor.nvidia.com/event",
                     meta={"source": "official_discovery", "event_id": "official-a", "date_confirmed": True})
    merged = pipeline._merge_official_events([yahoo], [official])
    assert len(merged) == 1 and merged[0].date == "2026-11-18"
    assert merged[0].meta["eps_estimate"] == 2.4
    assert merged[0].meta["date_confirmed"] is True and merged[0].title == "NVIDIA 财报"


def test_competing_ipo_estimates_are_reported_for_review():
    a = Event(date="2026-11-18", category="ipo", title="IPO", tickers=["ANT"], meta={"event_type": "pricing"})
    b = Event(date="2026-11-20", category="ipo", title="IPO", tickers=["ANT"], meta={"event_type": "pricing"})
    conflicts = []
    assert pipeline._merge_ipo_events([a], [b], conflicts) == [a]
    assert len(conflicts) == 1 and conflicts[0]["other_date"] == "2026-11-20"


def test_official_event_id_survives_reschedule_and_cancellation_without_duplicate():
    old = Event(date="2026-12-01", category="conference", title="AI Summit", meta={"event_id": "official-same"})
    changed = Event(date="2026-12-02", category="conference", title="AI Summit",
                    meta={"event_id": "official-same", "status": "cancelled"})
    merged = pipeline._merge_official_events([old], [changed])
    assert len(merged) == 1 and merged[0].date == "2026-12-02"
    assert merged[0].meta["status"] == "cancelled"


def _mock_composite_sources(monkeypatch, state):
    monkeypatch.setattr(pipeline, "ipo_events", lambda *a: [])
    monkeypatch.setattr(pipeline, "lockup_events", lambda *a: [])

    def yahoo(today, horizon, throttle=0.0):
        result = state.get("yahoo", "ok")
        record_status("earnings", "NVDA", result, fetched=int(result in {"ok", "empty"}),
                      accepted=int(result == "ok"))
        if result != "ok":
            return []
        return [Event(date=state.get("yahoo_date", "2026-11-23"), category="earnings",
                      title="NVIDIA 财报", tickers=["NVDA"],
                      source_url="https://finance.yahoo.com/quote/NVDA",
                      meta={"source": "earnings", "source_partition": "NVDA",
                            "ticker": "NVDA", "last_verified": today.isoformat(),
                            "date_confirmed": False, "eps_estimate": state.get("eps", 2.4),
                            "revenue_estimate": 100e9, "revenue_currency": "USD"})]

    def official(today, horizon, previous):
        result = state.get("official", "ok")
        record_status("official_discovery", "nvidia_events", result,
                      fetched=int(result in {"ok", "empty"}), accepted=int(result == "ok"))
        if result != "ok":
            return [], []
        return [Event(date=state.get("official_date", "2026-11-24"), category="earnings",
                      title="NVIDIA Financial Results", tickers=["NVDA"],
                      source_url="https://investor.nvidia.com/event",
                      meta={"source": "official_discovery", "source_partition": "nvidia_events",
                            "ticker": "NVDA", "event_id": "official-quarter-report",
                            "last_verified": today.isoformat(), "date_confirmed": True})], []

    monkeypatch.setattr(pipeline, "earnings_events", yahoo)
    monkeypatch.setattr(pipeline, "discover", official)


def _run_composite(tmp_path, day):
    a, b = pipeline.run(outputs_dir=tmp_path / "o", site_dir=tmp_path / "s", today=day)
    earnings = [event for event in a + b if event.category == "earnings" and "NVDA" in event.tickers]
    assert len(earnings) == 1
    report = json.loads((tmp_path / "o" / "source_status.json").read_text(encoding="utf-8"))
    return earnings[0], report


def test_official_date_and_yahoo_estimates_survive_alternating_source_outages(monkeypatch, tmp_path):
    state = {}
    _mock_composite_sources(monkeypatch, state)
    first, first_report = _run_composite(tmp_path, dt.date(2026, 11, 16))
    assert first.meta["date_last_verified"] == "2026-11-16"
    assert first.meta["estimates_last_verified"] == "2026-11-16"
    assert first_report["date_conflicts"][0]["resolution"] == "official_calendar"

    state.update(official="failed", eps=2.5)
    second, second_report = _run_composite(tmp_path, dt.date(2026, 11, 17))
    assert second.date == "2026-11-24" and second.meta["date_confirmed"] is True
    assert second.meta["eps_estimate"] == 2.5
    assert second.meta["date_source_stale"] is True
    assert second.meta["date_last_verified"] == "2026-11-16"
    assert second.meta["estimates_last_verified"] == "2026-11-17"
    assert second.meta["estimates_stale"] is False
    assert second_report["date_conflicts"][0]["resolution"] == "needs_review"

    state.update(official="ok", official_date="2026-11-25", yahoo="failed")
    third, _ = _run_composite(tmp_path, dt.date(2026, 11, 18))
    assert third.date == "2026-11-25" and third.meta["date_source_stale"] is False
    assert third.meta["date_last_verified"] == "2026-11-18"
    assert third.meta["eps_estimate"] == 2.5
    assert third.meta["estimates_stale"] is True
    assert third.meta["estimates_last_verified"] == "2026-11-17"

    state.update(yahoo="ok", eps=2.7)
    fourth, _ = _run_composite(tmp_path, dt.date(2026, 11, 19))
    assert fourth.meta["eps_estimate"] == 2.7
    assert not fourth.meta["date_source_stale"] and not fourth.meta["estimates_stale"]
    assert fourth.meta["date_last_verified"] == "2026-11-19"
    assert fourth.meta["estimates_last_verified"] == "2026-11-19"
    assert len({e.meta["ics_uid"] for e in (first, second, third, fourth)}) == 1
    assert fourth.meta["revision"] > first.meta["revision"]


def test_repeated_simultaneous_failure_does_not_refresh_component_verification(monkeypatch, tmp_path):
    state = {}
    _mock_composite_sources(monkeypatch, state)
    first, _ = _run_composite(tmp_path, dt.date(2026, 11, 16))
    state.update(official="failed", yahoo="failed")
    for day in (17, 18):
        event, _ = _run_composite(tmp_path, dt.date(2026, 11, day))
        assert event.date == first.date and event.meta["eps_estimate"] == first.meta["eps_estimate"]
        assert event.meta["date_source_stale"] and event.meta["estimates_stale"]
        assert event.meta["date_last_verified"] == "2026-11-16"
        assert event.meta["estimates_last_verified"] == "2026-11-16"


def test_successful_empty_yahoo_clears_only_estimates_during_official_outage(monkeypatch, tmp_path):
    state = {}
    _mock_composite_sources(monkeypatch, state)
    _run_composite(tmp_path, dt.date(2026, 11, 16))
    state.update(official="failed", yahoo="empty")
    event, _ = _run_composite(tmp_path, dt.date(2026, 11, 17))
    assert event.date == "2026-11-24" and event.meta["date_source_stale"] is True
    assert "eps_estimate" not in event.meta and "revenue_estimate" not in event.meta
    assert event.meta["estimates_stale"] is False


def test_expired_official_quarter_cannot_replace_a_new_future_yahoo_quarter(monkeypatch, tmp_path):
    state = {"official_date": "2026-11-18"}
    _mock_composite_sources(monkeypatch, state)
    first, _ = _run_composite(tmp_path, dt.date(2026, 11, 16))
    state.update(official="failed", yahoo_date="2026-12-15", eps=3.0)
    event, _ = _run_composite(tmp_path, dt.date(2026, 11, 19))
    assert event.date == "2026-12-15" and event.meta["eps_estimate"] == 3.0
    assert event.meta["date_source_type"] == "earnings"
    assert event.meta["ics_uid"] != first.meta["ics_uid"]


def test_legacy_combined_record_migrates_without_advancing_component_age():
    old = Event(date="2026-11-24", category="earnings", title="NVIDIA 财报", tickers=["NVDA"],
                source_url="https://investor.nvidia.com/event", meta={
                    "source": "official_discovery", "source_partition": "nvidia_events",
                    "last_verified": "2026-11-15", "eps_estimate": 2.4})
    fresh = Event(date="2026-11-24", category="earnings", title="NVIDIA 财报", tickers=["NVDA"],
                  source_url=old.source_url, meta={"source": "official_discovery",
                    "source_partition": "nvidia_events", "last_verified": "2026-11-16"})
    event = pipeline._restore_earnings_components([fresh], [old],
        [{"source": "earnings", "partition": "NVDA", "status": "failed"}],
        dt.date(2026, 11, 16), "2026-11-15")[0]
    assert event.meta["eps_estimate"] == 2.4 and event.meta["estimates_stale"] is True
    assert event.meta["estimates_last_verified"] == "2026-11-15"
    assert event.meta["date_last_verified"] == "2026-11-16"

