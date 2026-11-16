"""Tests for the unified event pipeline (earnings + IPO providers mocked)."""
import datetime as dt

from src import pipeline
from src.events_model import Event


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


def test_earnings_fallback_when_empty(monkeypatch, tmp_path):
    out = tmp_path / "o"
    out.mkdir()
    (out / "events.json").write_text(
        '{"events":[{"date":"2026-06-04","category":"earnings",'
        '"title":"Broadcom 财报","importance":3,"tickers":["AVGO"]}]}',
        encoding="utf-8")
    monkeypatch.setattr(pipeline, "earnings_events", lambda today, h, throttle=0.0: [])
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

