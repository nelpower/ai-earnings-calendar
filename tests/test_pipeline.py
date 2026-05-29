"""Tests for the window filtering (this week vs upcoming vs excluded)."""
import datetime as dt

from src import pipeline
from src.fetch_earnings import Earnings


def _e(ticker: str, day: dt.date) -> Earnings:
    return Earnings(ticker=ticker, name=ticker, subsector="s",
                    earnings_date=day.isoformat(), date_confirmed=True)


def test_window_filter(monkeypatch, tmp_path):
    today = dt.date(2026, 5, 29)
    items = [
        _e("TOMORROW", today + dt.timedelta(days=1)),   # this week
        _e("DAY7", today + dt.timedelta(days=7)),        # this week (edge)
        _e("DAY8", today + dt.timedelta(days=8)),        # upcoming
        _e("DAY30", today + dt.timedelta(days=30)),      # upcoming (edge)
        _e("TODAY", today),                              # excluded (day 0)
        _e("PAST", today - dt.timedelta(days=1)),        # excluded (reported)
        _e("DAY31", today + dt.timedelta(days=31)),      # excluded (too far)
    ]
    monkeypatch.setattr(pipeline, "fetch_all", lambda companies, throttle=0.0: items)
    monkeypatch.setattr(pipeline, "enrich_last_quarter_all",
                        lambda items, throttle=0.0: items)  # no network in tests
    monkeypatch.setattr(pipeline.config, "load_companies", lambda *a, **k: [{"ticker": "X"}])

    this_week, upcoming = pipeline.run(
        outputs_dir=tmp_path / "out", site_dir=tmp_path / "site", today=today)

    assert {e.ticker for e in this_week} == {"TOMORROW", "DAY7"}
    assert {e.ticker for e in upcoming} == {"DAY8", "DAY30"}
    # site + json were produced
    assert (tmp_path / "site" / "index.html").exists()
    assert (tmp_path / "out" / "earnings.json").exists()


def test_empty_fetch_falls_back(monkeypatch, tmp_path):
    # Pre-seed a "last good" earnings.json
    out = tmp_path / "out"
    out.mkdir()
    (out / "earnings.json").write_text(
        '{"all":[{"ticker":"AAA","name":"AAA","subsector":"s",'
        '"earnings_date":"2026-05-31","date_confirmed":true}]}', encoding="utf-8")
    monkeypatch.setattr(pipeline, "fetch_all", lambda companies, throttle=0.0: [])
    monkeypatch.setattr(pipeline.config, "load_companies", lambda *a, **k: [{"ticker": "X"}])

    this_week, _ = pipeline.run(
        outputs_dir=out, site_dir=tmp_path / "site", today=dt.date(2026, 5, 29))
    # fell back to committed data instead of publishing nothing
    assert {e.ticker for e in this_week} == {"AAA"}
