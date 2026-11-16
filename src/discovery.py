"""Bounded official-source discovery, without turning news dates into events.

News/RSS items are review candidates. Only verified calendar adapters and
schema.org Event.startDate may emit calendar Events. This module never follows
news links or uses an LLM, and does not persist anything; the pipeline owns state.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit, parse_qsl, urlencode, quote

import requests

from src import config
from src.events_model import Event

SOURCE_PATH = config.DATA_DIR / "discovery_sources.yaml"
WATCHLIST_PATH = config.DATA_DIR / "issuer_watchlist.yaml"
MAX_BYTES = 2_000_000
MAX_ITEMS = 24
MAX_CANDIDATES = 400
MAX_AGE_DAYS = 90
TIMEOUT = (5, 15)
_SEC_FORMS = {"S-1", "S-1/A", "F-1", "F-1/A", "424B4", "424B3",
              "8-K", "8-K/A", "6-K", "6-K/A"}
_AI_TERMS = re.compile(
    r"\bAI\b|artificial intelligence|semiconductor|\bchips?\b|export control|"
    r"data cent(?:er|re)|superintelligence|\bGPU\b|\bHBM\b|\bFOMC\b", re.I)
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
         "meta", "param", "source", "track", "wbr"}
_NAV_TITLES = {"announcements", "press releases", "all news", "latest news",
               "news", "research", "policy", "company", "product", "features",
               "agents", "economics", "science", "read more", "view all"}
_MONTHS = {m.lower(): i for i, m in enumerate(
    ("", "January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December")) if m}


def _health(source_id: str, status: str, fetched: int = 0, accepted: int = 0,
            detail: str = "", rejected: int = 0) -> None:
    # Import lazily so the adapter remains usable during standalone development.
    try:
        from src.source_health import record_status
    except ImportError:
        print(f"[discovery] {source_id}: {status}; {detail}")
        return
    record_status(source="official_discovery", partition=source_id, status=status,
                  fetched=fetched, accepted=accepted, detail=detail, rejected=rejected)


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _canonical(url: str) -> str:
    p = urlsplit(url)
    query = urlencode([(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                       if not k.lower().startswith("utm_")])
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path, query, ""))


def _allowed(url: str, source: dict) -> bool:
    try:
        p = urlsplit(url)
        domains = source.get("allowed_domains") or [urlsplit(source["url"]).hostname]
        return (p.scheme in {"https", "http"} and p.hostname in domains
                and p.username is None and p.password is None
                and p.port in {None, 443 if p.scheme == "https" else 80})
    except ValueError:
        return False


def _id(url: str) -> str:
    return "official-" + hashlib.sha256(_canonical(url).encode()).hexdigest()[:20]


def _date(value: str) -> str | None:
    """Parse an explicit full date, never an inferred year or a relative date."""
    value = _clean(value)
    if re.match(r"^\d{4}-\d{2}-\d{2}(?:$|T| )", value):
        try:
            return dt.date.fromisoformat(value[:10]).isoformat()
        except ValueError:
            return None
    try:
        return parsedate_to_datetime(value).date().isoformat()
    except (ValueError, TypeError, OverflowError):
        pass
    match = re.fullmatch(r"([A-Za-z]+) (\d{1,2})(?:st|nd|rd|th)?, (\d{4})", value)
    if match:
        month = next((i for m, i in _MONTHS.items()
                      if match[1].lower() in {m, m[:3]}), None)
        if month:
            try:
                return dt.date(int(match[3]), month, int(match[2])).isoformat()
            except ValueError:
                pass
    return None


@dataclass
class _Node:
    tag: str
    attrs: dict = field(default_factory=dict)
    parent: _Node | None = None
    children: list = field(default_factory=list)

    def text(self) -> str:
        if self.tag in {"script", "style", "nav", "header", "footer"}:
            return ""
        return _clean(" ".join(c.text() if isinstance(c, _Node) else c
                               for c in self.children))

    def nodes(self, tag: str = ""):
        for child in self.children:
            if isinstance(child, _Node):
                if not tag or child.tag == tag:
                    yield child
                yield from child.nodes(tag)

    def has_class(self, value: str) -> bool:
        return value in self.attrs.get("class", "").split()


class _HTML(HTMLParser):
    def __init__(self, html: str):
        super().__init__(convert_charrefs=True)
        self.root = _Node("root")
        self.stack = [self.root]
        self.count = 0
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.count += 1
        if self.count > 70_000 or len(self.stack) > 160:
            raise ValueError("HTML exceeds parser limits")
        node = _Node(tag, dict(attrs), self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in _VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def _fetch(source: dict) -> str:
    """One request per source; bounded bytes and redirects, exact host allowlist."""
    current = source["url"]
    for _ in range(4):
        if not _allowed(current, source):
            raise ValueError("redirect outside configured official domains")
        with requests.get(current, timeout=TIMEOUT, stream=True, allow_redirects=False,
                          headers={"User-Agent": source.get("user_agent", "Mozilla/5.0 AI-Market-Calendar/1.0"),
                                   "Accept": "application/json,text/html,application/rss+xml,application/xml"}) as resp:
            if resp.status_code in {301, 302, 303, 307, 308}:
                location = resp.headers.get("Location")
                if not location:
                    raise ValueError("redirect without Location")
                current = urljoin(current, location)
                continue
            resp.raise_for_status()
            chunks, size = [], 0
            for chunk in resp.iter_content(65_536):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ValueError("official source exceeds byte limit")
                chunks.append(chunk)
            content = b"".join(chunks)
            return content.decode("utf-8-sig", errors="replace")
    raise ValueError("too many redirects")


def _issuers() -> list[dict]:
    data = config.load_yaml(WATCHLIST_PATH)
    return data.get("issuers", []) if isinstance(data, dict) else []


def _matches(text: str, source: dict, issuers: list[dict]) -> list[str]:
    found = set(source.get("issuer_ids", []))
    for issuer in issuers:
        terms = [issuer.get("name", ""), *issuer.get("aliases", []),
                 *issuer.get("tickers", [])]
        if any(term and re.search(r"(?<!\w)" + re.escape(str(term)) + r"(?!\w)",
                                  text, re.I) for term in terms):
            found.add(issuer["id"])
    return sorted(found)


def _candidate(title: str, url: str, source: dict, today: dt.date,
               issuers: list[dict], published: str | None = None,
               context: str = "", reason: str = "", force_relevant: bool = False) -> dict | None:
    title = _clean(title)
    if not title or not _allowed(url, source):
        return None
    if published and (today - dt.date.fromisoformat(published)).days > MAX_AGE_DAYS:
        return None
    matched = _matches(title + " " + context, source, issuers)
    if not force_relevant and not matched and not _AI_TERMS.search(title + " " + context):
        return None
    return {"id": _id(url), "title": title[:500], "url": _canonical(url),
            "source_id": source["id"], "category": source["category"],
            "priority": source.get("priority", 2), "published_date": published,
            "first_seen": today.isoformat(), "last_seen": today.isoformat(),
            "matched_issuers": matched, "review_status": "needs_review",
            "reason": reason or "官方公告线索；发布日期不是活动日期，待核验具体日程。"}


def _event(title: str, url: str, date_value: str, source: dict, today: dt.date,
           horizon_days: int, issuers: list[dict], time_et: str = "",
           status: str = "confirmed") -> Event | None:
    day = _date(date_value)
    if not day or not today <= dt.date.fromisoformat(day) <= today + dt.timedelta(days=horizon_days):
        return None
    matched = _matches(title, source, issuers)
    if not matched and not _AI_TERMS.search(title):
        return None
    if not _allowed(url, source):
        return None
    tickers = sorted({t for i in issuers if i.get("id") in matched
                      for t in i.get("tickers", [])})
    category = source["category"]
    if re.search(r"financial results|earnings", title, re.I):
        category = "earnings"
    return Event(date=day, category=category, title=_clean(title),
                 importance=source.get("priority", 2), tickers=tickers,
                 time_et=time_et, source_url=_canonical(url),
                 watch="官方活动日程；关注议程、披露与后续安排，临近活动请复核来源。",
                 meta={"event_id": _id(url), "source": "official_discovery",
                       "source_partition": source["id"], "date_confirmed": status == "confirmed",
                       "last_verified": today.isoformat(), "status": status,
                       "matched_issuers": matched,
                       **({"ticker": tickers[0]} if category == "earnings" and tickers else {})})


def _rss(text: str, source: dict, today: dt.date, horizon: int,
         issuers: list[dict]) -> tuple[list[Event], list[dict], int]:
    root = ET.fromstring(text)
    if root.tag.rsplit("}", 1)[-1] not in {"rss", "feed"}:
        raise ValueError("expected RSS/Atom root")
    rows = root.findall("./channel/item") or root.findall("{http://www.w3.org/2005/Atom}entry")
    events, candidates = [], []
    for row in rows[:MAX_ITEMS]:
        fields = {c.tag.rsplit("}", 1)[-1]: c for c in row}
        def value(key):
            return (fields[key].text or "").strip() if key in fields else ""
        title = value("title")
        link = value("link") or (fields["link"].get("href", "") if "link" in fields else "")
        link = urljoin(source["url"], link)
        published = _date(value("pubDate") or value("published"))
        # Q4's verified event feed prefixes the event date in the title; pubDate
        # is the announcement date and MUST NOT be used for the event.
        match = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})\s*:\s*(.+)$", title)
        if source["kind"] == "q4_event_rss" and match:
            try:
                date_value = dt.date(int(match[3]), int(match[1]), int(match[2])).isoformat()
            except ValueError:
                date_value = ""
            event = _event(match[4], link, date_value, source, today, horizon, issuers)
            if event:
                events.append(event)
                continue
        description = value("description") or value("summary")
        context = _HTML(description).root.text() if "<" in description else description
        candidate = _candidate(title, link, source, today, issuers, published, context)
        if candidate:
            candidates.append(candidate)
    return events, candidates, len(rows)


def _sec_submissions(text: str, source: dict, today: dt.date, horizon: int,
                     issuers: list[dict]) -> tuple[list[Event], list[dict], int]:
    """Discover filing disclosures; filingDate never represents the event date."""
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("expected SEC submissions object")
    cik = str(data.get("cik", ""))
    expected_cik = str(source.get("cik", ""))
    if (not cik.isdigit() or not expected_cik.isdigit()
            or int(cik) != int(expected_cik)):
        raise ValueError("SEC submissions CIK missing or differs from configured issuer")
    matched = [i for i in issuers if i.get("id") in source.get("issuer_ids", [])]
    if not any(str(i.get("cik", "")).isdigit()
               and int(str(i["cik"])) == int(cik) for i in matched):
        raise ValueError("SEC CIK not independently identified in issuer watchlist")
    filings = data.get("filings")
    recent = filings.get("recent") if isinstance(filings, dict) else None
    fields = ("form", "filingDate", "accessionNumber", "primaryDocument")
    if not isinstance(recent, dict) or not all(isinstance(recent.get(k), list) for k in fields):
        raise ValueError("SEC recent filing arrays missing")
    lengths = {len(recent[k]) for k in fields}
    if len(lengths) != 1:
        raise ValueError("SEC recent filing arrays have inconsistent lengths")
    count = len(recent["form"])
    cutoff = today - dt.timedelta(days=MAX_AGE_DAYS)
    candidates = []
    name = str(data.get("name") or matched[0].get("name") or source["issuer_ids"][0])
    for form, filed, accession, document in zip(*(recent[k] for k in fields)):
        if not isinstance(form, str) or form.upper() not in _SEC_FORMS:
            continue
        filed_date = _date(filed) if isinstance(filed, str) else None
        if not filed_date:
            raise ValueError("SEC filingDate missing or invalid for relevant form")
        if not cutoff <= dt.date.fromisoformat(filed_date) <= today:
            continue
        if not isinstance(accession, str) or not re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession):
            raise ValueError("SEC accessionNumber invalid")
        if (not isinstance(document, str) or not document or document in {".", ".."}
                or any(c in document for c in ("/", "\\", "\r", "\n", "\x00"))):
            raise ValueError("SEC primaryDocument missing or unsafe")
        url = (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
               f"{accession.replace('-', '')}/{quote(document, safe='._-')}")
        lead = _candidate(f"{name} SEC {form.upper()} 申报", url, source, today, issuers,
                          published=filed_date,
                          reason="SEC 官方新申报线索；filingDate 是申报日期，不是上市、财报或其他活动日期。")
        if lead:
            lead.update(form=form.upper(), filing_date=filed_date, accession_number=accession)
            if form.upper().startswith(("S-1", "F-1", "424B")):
                lead["category"] = "ipo"
            candidates.append(lead)
        if len(candidates) >= MAX_ITEMS:
            break
    return [], candidates, count


def _publication(node: _Node) -> str | None:
    # Only explicit listing date fields; never search all prose for arbitrary dates.
    for current in node.nodes():
        if current.tag == "time" and current.attrs.get("datetime"):
            return _date(current.attrs["datetime"])
        if current.has_class("col-auto"):
            day = _date(current.text())
            if day:
                return day
    return None


def _links(root: _Node, source: dict, today: dt.date,
           issuers: list[dict]) -> tuple[list[dict], int]:
    found = {}
    inspected = set()
    for anchor in root.nodes("a"):
        url = _canonical(urljoin(source["url"], anchor.attrs.get("href", "")))
        if not any(urlsplit(url).path.startswith(p) for p in source.get("link_prefixes", [])):
            continue
        title = anchor.text()
        excluded = {p.rstrip("/") for p in source.get("excluded_paths", [])}
        if (len(title) < 12 or title.casefold() in _NAV_TITLES
                or urlsplit(url).path.rstrip("/") in excluded):
            continue
        inspected.add(url)
        container = anchor.parent or anchor
        for _ in range(5):
            if container.tag in {"article", "li", "tr"} or _publication(container):
                break
            if not container.parent or len(container.parent.text()) > 12_000:
                break
            container = container.parent
        candidate = _candidate(title, url, source, today, issuers,
                               _publication(container), container.text()[:6000])
        if candidate and (url not in found or len(title) < len(found[url]["title"])):
            found[url] = candidate
        if len(inspected) >= MAX_ITEMS:
            break
    return list(found.values()), len(inspected)


def _structured(root: _Node, source: dict, today: dt.date, horizon: int,
                issuers: list[dict]) -> tuple[list[Event], list[dict]]:
    events, candidates = [], []
    unkeyed_titles: dict[str, int] = {}

    def review(value: dict, reason: str) -> None:
        title = str(value.get("name", ""))
        lead = _candidate(title, source["url"], source, today, issuers, reason=reason)
        if lead:
            # Unkeyed schema records must not collapse merely because they share
            # one listing URL. This is a review identity, not a calendar UID.
            # Preserve title occurrences without putting the mutable date in it.
            key = title.casefold().strip()
            occurrence = unkeyed_titles.get(key, 0)
            unkeyed_titles[key] = occurrence + 1
            seed = f"{_canonical(source['url'])}|schema-review|{key}|{occurrence}"
            lead["id"] = "official-" + hashlib.sha256(seed.encode()).hexdigest()[:20]
            if isinstance(value.get("startDate"), str):
                lead["observed_start_date"] = value["startDate"][:100]
            candidates.append(lead)

    # schema.org Article.datePublished is deliberately ignored.
    def visit(value):
        if isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            types = value.get("@type", [])
            types = types if isinstance(types, list) else [types]
            if any(str(t).rsplit("/", 1)[-1] == "Event" for t in types):
                raw_url = value.get("url")
                raw_id = value.get("@id")
                event_url = source["url"]
                identity = ""
                unsafe = False
                if isinstance(raw_url, str) and raw_url:
                    resolved = urljoin(source["url"], raw_url)
                    unsafe = not _allowed(resolved, source)
                    if not unsafe:
                        event_url = resolved
                        # A listing URL reused by several Events is not an
                        # independent event identity, unless accompanied by @id.
                        if (_canonical(resolved) != _canonical(source["url"])
                                or urlsplit(resolved).fragment):
                            identity = resolved
                if isinstance(raw_id, str) and raw_id:
                    if re.fullmatch(r"urn:uuid:[0-9a-fA-F-]{36}", raw_id):
                        identity = raw_id
                    else:
                        resolved_id = urljoin(source["url"], raw_id)
                        if not _allowed(resolved_id, source):
                            unsafe = True
                        else:
                            identity = resolved_id  # Retain fragments for identity.
                            if not raw_url:
                                event_url = resolved_id
                if unsafe:
                    review(value, "官方页面中的活动链接或标识不在允许的官方域内，需人工核验。")
                    return
                if not identity:
                    review(value, "官方结构化活动缺少独立 URL/@id；保留线索，待核验身份和日程。")
                    return
                status = "cancelled" if str(value.get("eventStatus", "")).endswith("EventCancelled") else "confirmed"
                if str(value.get("eventStatus", "")).endswith(("EventPostponed", "EventRescheduled")):
                    status = "proposed"
                event = _event(str(value.get("name", "")),
                               event_url,
                               str(value.get("startDate", "")), source, today, horizon,
                               issuers, status=status)
                if event:
                    seed = f"{source['id']}|schema-event|{identity}"
                    event.meta["event_id"] = "official-" + hashlib.sha256(seed.encode()).hexdigest()[:20]
                    events.append(event)
                elif not _date(str(value.get("startDate", ""))):
                    review(value, "官方结构化活动的日期缺失或不明确，待人工核验。")
            if "@graph" in value:
                visit(value["@graph"])
    for node in root.nodes("script"):
        if node.attrs.get("type", "").lower() == "application/ld+json":
            try:
                visit(json.loads("".join(c for c in node.children if isinstance(c, str))))
            except (ValueError, TypeError):
                continue
    return events, candidates


def _html(text: str, source: dict, today: dt.date, horizon: int,
          issuers: list[dict]) -> tuple[list[Event], list[dict], int]:
    root = _HTML(text).root
    kind = source["kind"]
    if kind == "schedule_snapshot":
        body = root.text()
        if not all(term.casefold() in body.casefold() for term in source.get("expected_terms", [])):
            raise ValueError("official schedule markers missing (blocked or changed HTML)")
        candidate = _candidate(source["title"], source["url"], source, today, issuers,
                               context=body[:3000],
                               reason="官方专门日程复核：核对已录日期、年份、改期与新增条目；不将网页更新时间当活动日期。",
                               force_relevant=True)
        if candidate:
            candidate["content_hash"] = hashlib.sha256(body.encode()).hexdigest()[:20]
        return [], [candidate] if candidate else [], 1
    events, structured_leads = _structured(root, source, today, horizon, issuers)
    structured_count = len(events) + len(structured_leads)
    if kind in {"amd_calendar", "banking_calendar"}:
        rows = [n for n in root.nodes() if
                (kind == "amd_calendar" and n.tag == "article" and n.has_class("media"))
                or (kind == "banking_calendar" and n.tag == "tr" and n.has_class("vevent"))]
        if not rows:
            raise ValueError("official calendar rows missing")
        candidates = list(structured_leads)
        for row in rows[:MAX_ITEMS]:
            time = next((n for n in row.nodes("time") if n.attrs.get("datetime")), None)
            heading = next(row.nodes("h3"), None)
            anchor = next((n for n in row.nodes("a") if
                           (kind == "amd_calendar" and "/ical?" in n.attrs.get("href", ""))
                           or (kind == "banking_calendar" and n.has_class("summary"))), None)
            title = (heading or anchor).text() if heading or anchor else ""
            url = urljoin(source["url"], anchor.attrs["href"]) if anchor else source["url"]
            event = _event(title, url, time.attrs["datetime"] if time else "", source,
                           today, horizon, issuers,
                           time_et=time.attrs["datetime"][11:16] if time and kind == "banking_calendar" else "")
            if event:
                events.append(event)
            elif not time:
                candidate = _candidate(title, url, source, today, issuers,
                                       reason="官方活动列表的日期字段缺失，需人工核验。")
                if candidate:
                    candidates.append(candidate)
        return events, candidates, len(rows) + structured_count
    candidates, fetched = _links(root, source, today, issuers)
    candidates += structured_leads
    if not candidates and not events:
        # No relevant items may be normal, but only after verifying the listing
        # still contains links in the configured paths rather than a bot wall.
        if not any(any(urlsplit(urljoin(source["url"], n.attrs.get("href", ""))).path.startswith(p)
                       for p in source.get("link_prefixes", [])) for n in root.nodes("a")):
            raise ValueError("official listing links missing")
    return events, candidates, fetched + structured_count


def discover(today: dt.date, horizon_days: int,
             previous_candidates: list[dict] | None = None) -> tuple[list[Event], list[dict]]:
    """Return verified events and a persistent review queue of official leads.

    Unseen previous candidates remain visible (e.g. a source failed). Human
    review decisions and first_seen survive refresh; content_hash detects changes
    to dedicated schedules without claiming those changes are new event dates.
    """
    issuers = _issuers()
    if not issuers:
        _health("issuer_watchlist", "invalid_schema",
                detail="Issuer watchlist missing or empty; entity matching/tickers unavailable")
    configured = config.load_yaml(SOURCE_PATH)
    sources = configured.get("sources", []) if isinstance(configured, dict) else []
    if not sources:
        _health("configuration", "invalid_schema", detail="No discovery sources configured")
    previous = {c["id"]: dict(c) for c in previous_candidates or [] if c.get("id")}
    candidates = dict(previous)
    events = {}
    for raw in sources:
        source = dict(raw)
        source["url"] = source["url"].format(year=today.year)
        sid = source["id"]
        try:
            text = _fetch(source)
        except Exception as exc:
            _health(sid, "failed", detail=f"{type(exc).__name__}: {str(exc)[:220]}")
            continue
        try:
            parser = (_sec_submissions if source["kind"] == "sec_submissions" else
                      _rss if source["kind"] in {"rss", "q4_event_rss"} else _html)
            accepted_events, leads, fetched = parser(text, source, today, horizon_days, issuers)
        except Exception as exc:
            _health(sid, "invalid_schema", detail=f"{type(exc).__name__}: {str(exc)[:220]}")
            continue
        for event in accepted_events:
            events[event.meta["event_id"]] = event
        for lead in leads:
            old = previous.get(lead["id"], {})
            lead["first_seen"] = old.get("first_seen", lead["first_seen"])
            lead["review_status"] = old.get("review_status", "needs_review")
            if old.get("content_hash") and lead.get("content_hash") != old["content_hash"]:
                lead["review_status"] = "needs_review"
                lead["reason"] += " 官方日程内容自上次观察已变更。"
            for key in ("reviewed_at", "review_note"):
                if key in old:
                    lead[key] = old[key]
            candidates[lead["id"]] = lead
        count = len(accepted_events) + len(leads)
        _health(sid, "ok" if fetched else "empty", fetched=fetched, accepted=count,
                rejected=max(0, min(fetched, MAX_ITEMS) - count),
                detail=f"{len(accepted_events)} verified calendar event(s); {len(leads)} review lead(s)")
    queue = sorted(candidates.values(), key=lambda c: (
        c.get("last_seen", ""), c.get("priority", 0), c.get("id", "")), reverse=True)
    return sorted(events.values(), key=lambda e: (e.date, e.title)), queue[:MAX_CANDIDATES]
