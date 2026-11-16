"""Official-source fixtures: no network, no publication-date event inference."""
import datetime as dt
import json

import pytest
import requests

from src import discovery, source_health

TODAY = dt.date(2026, 11, 16)
ISSUERS = [
    {"id": "nvidia", "name": "NVIDIA", "aliases": ["Jensen Huang"], "tickers": ["NVDA"], "cik": "0001045810"},
    {"id": "amd", "name": "Advanced Micro Devices", "aliases": ["AMD"], "tickers": ["AMD"], "cik": "0000002488"},
    {"id": "anthropic", "name": "Anthropic", "aliases": ["Claude"], "tickers": [],
     "proposed_ticker": "ANT", "listed": False},
]


class Response:
    def __init__(self, body="", status=200, headers=None):
        self.body = body.encode() if isinstance(body, str) else body
        self.status_code = status
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def iter_content(self, size):
        for i in range(0, len(self.body), size):
            yield self.body[i:i + size]


def source(kind="rss", **changes):
    return {"id": "fixture", "url": "https://official.example/news/rss.xml",
            "kind": kind, "category": "conference", "priority": 3,
            "allowed_domains": ["official.example"], **changes}


def setup(monkeypatch, sources, bodies):
    def load(path):
        if path == discovery.SOURCE_PATH:
            return {"sources": sources}
        if path == discovery.WATCHLIST_PATH:
            return {"issuers": ISSUERS}
        raise AssertionError(path)

    calls = []
    def get(url, **kwargs):
        calls.append(url)
        assert kwargs["timeout"] == discovery.TIMEOUT
        assert kwargs["allow_redirects"] is False
        body = bodies[url]
        if isinstance(body, Exception):
            raise body
        return body if isinstance(body, Response) else Response(body)

    monkeypatch.setattr(discovery.config, "load_yaml", load)
    monkeypatch.setattr(discovery.requests, "get", get)
    source_health.reset_health(TODAY)
    return calls


def test_news_publication_date_is_only_a_candidate_even_if_in_window(monkeypatch):
    src = source()
    rss = """<rss><channel><item><title>NVIDIA announces a hearing next week</title>
      <link>https://official.example/news/hearing</link>
      <pubDate>Wed, 18 Nov 2026 17:00:00 -0500</pubDate>
      <description>Expected next week; no confirmed date.</description>
      </item></channel></rss>"""
    setup(monkeypatch, [src], {src["url"]: rss})
    events, leads = discovery.discover(TODAY, 45)
    assert events == [] and len(leads) == 1
    assert leads[0]["published_date"] == "2026-11-18"
    assert leads[0]["review_status"] == "needs_review"
    assert leads[0]["matched_issuers"] == ["nvidia"]


def test_q4_event_date_prefix_is_distinct_from_rss_publication_date(monkeypatch):
    src = source("q4_event_rss", issuer_ids=["nvidia"])
    rss = """<rss><channel><item>
      <title>11/17/2026 : NVIDIA 3rd Quarter FY27 Financial Results</title>
      <link>https://official.example/events/fy27-q3</link>
      <pubDate>Wed, 28 Oct 2026 17:00:15 -0400</pubDate>
      </item></channel></rss>"""
    setup(monkeypatch, [src], {src["url"]: rss})
    events, leads = discovery.discover(TODAY, 45)
    assert not leads and len(events) == 1
    e = events[0]
    assert e.date == "2026-11-17" and e.category == "earnings"
    assert e.time_et == "" and e.tickers == ["NVDA"]
    assert e.meta["date_confirmed"] is True
    assert e.meta["last_verified"] == "2026-11-16"
    assert e.meta["source_partition"] == src["id"]


def test_q4_missing_date_remains_review_candidate(monkeypatch):
    src = source("q4_event_rss", issuer_ids=["nvidia"])
    rss = """<rss><channel><item><title>NVIDIA investor event</title>
      <link>https://official.example/event</link><pubDate>Tue, 17 Nov 2026 12:00:00 GMT</pubDate>
      </item></channel></rss>"""
    setup(monkeypatch, [src], {src["url"]: rss})
    events, leads = discovery.discover(TODAY, 45)
    assert not events and len(leads) == 1


def test_press_release_replaces_neither_event_date_nor_human_review(monkeypatch):
    src = source("html", category="policy", link_prefixes=["/news/press-releases/"])
    html = """<article><h3><a href="/news/press-releases/jensen">
      Request Jensen Huang Brief Congress</a></h3><div class="col-auto">November 12, 2026</div>
      <p>Requested December 1; subject to discussions.</p></article>"""
    setup(monkeypatch, [src], {src["url"]: html})
    _, leads = discovery.discover(TODAY, 45)
    old = {**leads[0], "first_seen": "2026-11-13", "review_status": "reviewed",
           "review_note": "Date remains tentative"}
    events, leads = discovery.discover(TODAY, 45, [old])
    assert not events and len(leads) == 1
    assert leads[0]["published_date"] == "2026-11-12"
    assert leads[0]["first_seen"] == "2026-11-13"
    assert leads[0]["last_seen"] == "2026-11-16"
    assert leads[0]["review_status"] == "reviewed"
    assert leads[0]["review_note"] == old["review_note"]


def test_amd_structured_calendar_ignores_midnight_as_real_clock_time(monkeypatch):
    src = source("amd_calendar", issuer_ids=["amd"])
    html = """<h2>Upcoming Events</h2><article class="media"><div class="media-body">
      <time datetime="2026-12-01T00:00:00">Dec 1, 2026</time>
      <h3>UBS Global Technology and AI Conference</h3><a href="/ical?id=9249">Add to Calendar</a>
      </div></article>"""
    setup(monkeypatch, [src], {src["url"]: html})
    events, leads = discovery.discover(TODAY, 45)
    assert not leads and len(events) == 1
    assert events[0].date == "2026-12-01"
    assert events[0].time_et == "" and events[0].tickers == ["AMD"]


def test_banking_hearing_uses_dtstart_not_date_in_url_or_unrelated_nomination(monkeypatch):
    src = source("banking_calendar", category="policy")
    html = """<table><tr class="vevent"><td><a class="summary url"
      href="/hearings/11/12/2026/ai-hearing">AI and the American Dream</a></td>
      <td><time class="dtstart" datetime="2026-11-19T10:00">11/19/26 10:00AM</time></td></tr>
      <tr class="vevent"><td><a class="summary" href="/hearings/nominations">Nomination Hearing</a></td>
      <td><time datetime="2026-11-20T10:00">11/20/26</time></td></tr></table>"""
    setup(monkeypatch, [src], {src["url"]: html})
    events, leads = discovery.discover(TODAY, 45)
    assert not leads and len(events) == 1
    assert events[0].date == "2026-11-19" and events[0].time_et == "10:00"


def test_jsonld_event_allowed_but_article_datepublished_is_not(monkeypatch):
    src = source("html", issuer_ids=["nvidia"], link_prefixes=["/news/"])
    html = """<script type="application/ld+json">{"@graph":[
      {"@type":"NewsArticle","headline":"NVIDIA news","datePublished":"2026-11-18"},
      {"@type":"Event","name":"NVIDIA AI Developer Conference","startDate":"2026-11-20T09:00:00-08:00",
       "url":"https://official.example/events/ai"}]}</script>"""
    setup(monkeypatch, [src], {src["url"]: html})
    events, _ = discovery.discover(TODAY, 45)
    assert len(events) == 1 and events[0].date == "2026-11-20"
    assert events[0].time_et == ""  # Pacific time is not mislabeled Eastern.


def test_schedule_without_year_is_a_review_and_changes_reopen_it(monkeypatch):
    src = source("schedule_snapshot", category="ipo", issuer_ids=["anthropic"],
                 title="Anthropic IPO 官方日程复核", expected_terms=["IPO", "publicly trading"])
    html = "<p>IPO final price November 18th; publicly trading November 19th, expected.</p>"
    setup(monkeypatch, [src], {src["url"]: html})
    events, leads = discovery.discover(TODAY, 45)
    assert not events and len(leads) == 1
    old = {**leads[0], "review_status": "reviewed"}
    setup(monkeypatch, [src], {src["url"]: html.replace("19th", "20th")})
    events, leads = discovery.discover(TODAY, 45, [old])
    assert not events and leads[0]["review_status"] == "needs_review"
    assert leads[0]["content_hash"] != old["content_hash"]
    assert leads[0]["matched_issuers"] == ["anthropic"]


def test_http_failure_retains_previous_candidates_and_is_reported(monkeypatch):
    src = source()
    setup(monkeypatch, [src], {src["url"]: Response(status=403)})
    old = {"id": "known", "title": "Known official lead", "last_seen": "2026-11-15"}
    events, leads = discovery.discover(TODAY, 45, [old])
    assert not events and leads == [old]
    report = source_health.reports()[0]
    assert report["source"] == "official_discovery" and report["status"] == "failed"
    assert "403" in report["detail"]


@pytest.mark.parametrize("body,kind", [
    ("<html>Just a moment. Enable JavaScript.</html>", "html"),
    ("<html>Rate limited</html>", "rss"),
    ("<rss><channel><item></rss>", "rss"),
    ("<html>No calendar rows</html>", "amd_calendar"),
])
def test_bot_wall_and_changed_schema_are_not_successful_empty(monkeypatch, body, kind):
    src = source(kind, link_prefixes=["/news/"])
    setup(monkeypatch, [src], {src["url"]: body})
    assert discovery.discover(TODAY, 45) == ([], [])
    assert source_health.reports()[0]["status"] == "invalid_schema"


def test_off_domain_redirect_is_blocked_before_second_request(monkeypatch):
    src = source()
    calls = setup(monkeypatch, [src], {src["url"]: Response(
        status=302, headers={"Location": "https://not-official.example/mirror"})})
    assert discovery.discover(TODAY, 45) == ([], [])
    assert calls == [src["url"]]
    assert source_health.reports()[0]["status"] == "failed"


def test_macro_schedule_candidate_is_forced_relevant_without_issuer_match(monkeypatch):
    src = source("schedule_snapshot", category="macro", title="BEA 官方日程复核",
                 expected_terms=["Release Schedule", "Personal Income and Outlays"])
    setup(monkeypatch, [src], {src["url"]: "<h1>Release Schedule</h1><p>Personal Income and Outlays</p>"})
    events, leads = discovery.discover(TODAY, 45)
    assert not events and len(leads) == 1
    assert leads[0]["title"] == src["title"] and leads[0]["matched_issuers"] == []


def test_official_listing_reports_non_ai_rows_as_rejected(monkeypatch):
    src = source("html", category="policy", link_prefixes=["/news/press-releases/"])
    html = """<article><h3><a href="/news/press-releases/no-ai">
      Statement on Humanitarian Assistance</a></h3><time datetime="2026-11-15">15 November</time></article>
      <article><h3><a href="/news/press-releases/jensen">Jensen Huang Congressional Request</a></h3>
      <time datetime="2026-11-12">12 November</time></article>"""
    setup(monkeypatch, [src], {src["url"]: html})
    events, leads = discovery.discover(TODAY, 45)
    assert not events and len(leads) == 1
    report = source_health.reports()[0]
    assert report["fetched"] == 2 and report["accepted"] == 1 and report["rejected"] == 1


def test_large_response_stops_at_byte_limit(monkeypatch):
    src = source()
    setup(monkeypatch, [src], {src["url"]: "x" * 200})
    monkeypatch.setattr(discovery, "MAX_BYTES", 100)
    assert discovery.discover(TODAY, 45) == ([], [])
    assert "byte limit" in source_health.reports()[0]["detail"]


def sec_source():
    return source("sec_submissions", url="https://data.sec.gov/submissions/CIK0001045810.json",
                  issuer_ids=["nvidia"], cik="0001045810",
                  allowed_domains=["data.sec.gov", "www.sec.gov"],
                  user_agent="AI-Market-Calendar/1.0 (https://github.com/nelpower/ai-earnings-calendar)")


def sec_body():
    return {"cik": "1045810", "name": "NVIDIA CORP", "filings": {"recent": {
        "form": ["8-K", "S-1/A", "4", "6-K", "424B4"],
        "filingDate": ["2026-11-12", "2026-10-30", "2026-11-15", "2026-01-01", "2026-11-17"],
        "accessionNumber": ["0001045810-26-000082", "0001045810-26-000080", "invalid", "invalid", "invalid"],
        "primaryDocument": ["nvda-20261111.htm", "registration.htm", "ownership.xml", "old.htm", "future.htm"],
    }}}


def test_sec_filing_date_is_only_a_disclosure_candidate_and_forms_are_filtered(monkeypatch):
    src = sec_source()
    setup(monkeypatch, [src], {src["url"]: json.dumps(sec_body())})
    events, leads = discovery.discover(TODAY, 45)
    assert not events and len(leads) == 2
    by_form = {lead["form"]: lead for lead in leads}
    lead = by_form["8-K"]
    assert lead["url"] == "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000082/nvda-20261111.htm"
    assert lead["published_date"] == lead["filing_date"] == "2026-11-12"
    assert lead["review_status"] == "needs_review"
    assert lead["matched_issuers"] == ["nvidia"]
    assert by_form["S-1/A"]["category"] == "ipo"
    assert source_health.reports()[0]["status"] == "ok"


@pytest.mark.parametrize("change", ["missing_field", "length", "cik", "unsafe_document"])
def test_sec_invalid_submissions_structure_is_visible(monkeypatch, change):
    src = sec_source()
    body = sec_body()
    if change == "missing_field":
        del body["filings"]["recent"]["filingDate"]
    elif change == "length":
        body["filings"]["recent"]["form"].pop()
    elif change == "cik":
        body["cik"] = "2488"
    else:
        body["filings"]["recent"]["primaryDocument"][0] = "../redirect.htm"
    setup(monkeypatch, [src], {src["url"]: json.dumps(body)})
    assert discovery.discover(TODAY, 45) == ([], [])
    assert source_health.reports()[0]["status"] == "invalid_schema"


def test_sec_403_does_not_become_an_empty_calendar(monkeypatch):
    src = sec_source()
    setup(monkeypatch, [src], {src["url"]: Response(status=403)})
    assert discovery.discover(TODAY, 45) == ([], [])
    assert source_health.reports()[0]["status"] == "failed"


def test_unkeyed_schema_events_remain_distinct_review_leads(monkeypatch):
    src = source("html", issuer_ids=["nvidia"], link_prefixes=["/news/"])
    html = '<script type="application/ld+json">' + json.dumps({"@graph": [
        {"@type": "Event", "name": "NVIDIA first event", "startDate": "2026-11-18"},
        {"@type": "Event", "name": "NVIDIA second event", "startDate": "2026-11-19"},
        {"@type": "Event", "name": "NVIDIA first event", "startDate": "2026-11-20"},
    ]}) + '</script>'
    setup(monkeypatch, [src], {src["url"]: html})
    events, leads = discovery.discover(TODAY, 45)
    assert not events and len(leads) == 3
    assert len({c["id"] for c in leads}) == 3
    assert all(c["review_status"] == "needs_review" for c in leads)
    assert all(c["url"] == src["url"] for c in leads)
    assert {c["observed_start_date"] for c in leads} == {"2026-11-18", "2026-11-19", "2026-11-20"}
    assert source_health.reports()[0]["status"] == "ok"


def test_schema_fragment_ids_distinguish_events_and_survive_date_changes(monkeypatch):
    src = source("html", issuer_ids=["nvidia"], link_prefixes=["/news/"])
    records = [{"@type": "Event", "@id": "#first", "name": "NVIDIA first event", "startDate": "2026-11-18"},
               {"@type": "Event", "@id": "#second", "name": "NVIDIA second event", "startDate": "2026-11-19"}]
    def page():
        return '<script type="application/ld+json">' + json.dumps(records) + '</script>'
    setup(monkeypatch, [src], {src["url"]: page()})
    events, leads = discovery.discover(TODAY, 45)
    assert len(events) == 2 and not leads
    before = {e.title: e.meta["event_id"] for e in events}
    assert len(set(before.values())) == 2
    records[0]["startDate"] = "2026-11-20"
    setup(monkeypatch, [src], {src["url"]: page()})
    after, _ = discovery.discover(TODAY, 45)
    assert {e.title: e.meta["event_id"] for e in after} == before


def test_schema_off_domain_identity_is_reviewed_using_safe_source_url(monkeypatch):
    src = source("html", issuer_ids=["nvidia"], link_prefixes=["/news/"])
    html = '<script type="application/ld+json">' + json.dumps({
        "@type": "Event", "@id": "https://unofficial.example/ai", "name": "NVIDIA event",
        "url": "https://official.example/event", "startDate": "2026-11-18"}) + '</script>'
    setup(monkeypatch, [src], {src["url"]: html})
    events, leads = discovery.discover(TODAY, 45)
    assert not events and len(leads) == 1
    assert leads[0]["url"] == src["url"]


@pytest.mark.parametrize("url", [
    "https://:password@official.example/news", "https://user@official.example/news",
    "https://@official.example/news", "https://official.example:8443/news",
    "https://official.example:80/news", "http://official.example:443/news",
    "https://official.example:invalid/news", "https://official.example:not-a-port/news",
    "javascript:alert(1)", "https://official.example.evil.test/news",
])
def test_url_allowlist_rejects_credentials_ports_and_fake_domains(url):
    assert discovery._allowed(url, source()) is False


@pytest.mark.parametrize("url", [
    "https://official.example/news", "https://official.example:443/news",
    "http://official.example:80/news",
])
def test_url_allowlist_accepts_only_normal_official_urls(url):
    assert discovery._allowed(url, source()) is True


def test_category_navigation_is_not_a_news_candidate(monkeypatch):
    src = source("html", issuer_ids=["nvidia"], link_prefixes=["/news/"],
                 excluded_paths=["/news/research-hub"])
    html = """<main><a href="/news/announcements">Announcements</a>
      <a href="/news/research-hub/">Explore Our Research Hub</a>
      <article><a href="/news/announcements-from-the-conference">Announcements from the NVIDIA AI Conference</a></article>
      </main>"""
    setup(monkeypatch, [src], {src["url"]: html})
    events, leads = discovery.discover(TODAY, 45)
    assert not events and len(leads) == 1
    assert leads[0]["title"] == "Announcements from the NVIDIA AI Conference"
