"""Tests for the unified event pipeline (earnings + IPO providers mocked)."""
import datetime as dt

from src import pipeline


def test_run_merges_and_windows(monkeypatch, tmp_path):
    today = dt.date(2026, 5, 29)
    # mock the network providers so the test is offline & deterministic
    monkeypatch.setattr(pipeline, "earnings_events", lambda today, h, throttle=0.0: [])
    monkeypatch.setattr(pipeline, "ipo_events", lambda today, h: [])

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

    this_week, upcoming = pipeline.run(
        outputs_dir=out, site_dir=tmp_path / "s", today=dt.date(2026, 5, 29))
    all_titles = " ".join(e.title for e in this_week + upcoming)
    assert "Broadcom" in all_titles    # reused last-good earnings
