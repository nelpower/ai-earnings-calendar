"""Offline integrity checks for the earnings universe and issuer registry.

The original 114 rows predate per-row provenance. New additions must carry
business evidence and SEC identifier evidence; unlisted brands never become
earnings fetch targets just because a proposed symbol appears in an SEC file.
"""
import datetime as dt
import re
from pathlib import Path
from urllib.parse import urlsplit

import yaml

DATA = Path(__file__).resolve().parents[1] / "data"
LEGACY_TICKERS = set("""NVDA AMD AVGO MRVL ARM ALAB INTC QCOM TSM MU GFS MPWR LSCC RMBS CRDO SITM AMBA SLAB SMTC POWI MTSI UMC ASX ASML AMAT LRCX KLAC TER ENTG ONTO FORM ACLS NVMI CAMT AEHR KLIC AMKR ICHR UCTT SNPS CDNS ANET CSCO CIEN COHR LITE AAOI FN SMCI DELL HPE VRT ETN NVT GEV GNRC POWL WDC STX NTAP MSFT GOOGL AMZN META ORCL IBM PLTR CRM NOW SNOW AI ADBE CRWD PANW S NET DDOG MDB PATH GTLB ESTC BBAI SOUN TWLO ZS OKTA RBRK IOT GWRE WDAY INTU TEAM HUBS DT FTNT TENB QLYS APP PEGA DOCN FROG CRWV NBIS APLD IREN WULF CEG VST TLN NRG OKLO SMR AAPL TSLA""".split())


def _companies():
    return yaml.safe_load((DATA / "ai_companies.yaml").read_text(encoding="utf-8"))["companies"]


def _issuers():
    return yaml.safe_load((DATA / "issuer_watchlist.yaml").read_text(encoding="utf-8"))["issuers"]


def _assert_source(url):
    parsed = urlsplit(url)
    assert parsed.scheme == "https" and parsed.hostname, url


def _assert_review_date(value):
    # A persisted review date must be ISO, rather than an implicit "today".
    assert isinstance(value, str)
    assert dt.date.fromisoformat(value) <= dt.date.today()


def test_company_identifiers_are_unique_and_required_fields_present():
    companies = _companies()
    symbols = [row["ticker"].upper() for row in companies]
    assert len(symbols) == len(set(symbols))
    for row in companies:
        assert re.fullmatch(r"[A-Z0-9]+(?:[.-][A-Z0-9]+)*", row["ticker"])
        assert row["name"].strip()
        assert row["subsector"].strip()


def test_new_companies_have_official_business_and_identifier_provenance():
    additions = [row for row in _companies() if row["ticker"] not in LEGACY_TICKERS]
    assert additions
    for row in additions:
        _assert_source(row["source_url"])
        assert row["ticker_source_url"] == "https://www.sec.gov/files/company_tickers.json"
        assert re.fullmatch(r"\d{10}", row["cik"])
        _assert_review_date(row["verified_on"])
        assert row["relevance"].strip()


def test_expansion_covers_material_supply_chain_and_current_identifiers():
    symbols = {row["ticker"] for row in _companies()}
    # Storage split/name change and two completed 2026 IPOs are easy to miss.
    assert {"SNDK", "P", "CBRS", "SPCX"} <= symbols
    assert {"ADI", "NVTS", "AXTI", "ACMR", "TEL", "TT", "MOD",
            "NTNX", "SAP", "BABA", "BIDU", "HIMX"} <= symbols
    assert {"MBLY", "CGNX", "SYM", "ROK", "DLR", "EQIX", "VNET", "GDS"} <= symbols
    assert "PSTG" not in symbols
    assert "QRVO" not in symbols  # acquisition closed 2026-10-05


def test_issuer_ids_aliases_and_required_fields_are_valid():
    issuers = _issuers()
    ids = [issuer["id"] for issuer in issuers]
    assert len(ids) == len(set(ids))
    for issuer in issuers:
        assert re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", issuer["id"])
        assert issuer["name"].strip()
        assert type(issuer["listed"]) is bool
        assert issuer["priority"] in (2, 3)
        assert issuer["aliases"] and all(str(alias).strip() for alias in issuer["aliases"])
        aliases = [alias.casefold() for alias in issuer["aliases"]]
        assert len(aliases) == len(set(aliases))
        assert isinstance(issuer["tickers"], list)
        assert issuer["official_urls"]
        for url in issuer["official_urls"]:
            _assert_source(url)
        _assert_source(issuer["source_url"])
        _assert_review_date(issuer["verified_on"])
        if issuer.get("cik"):
            assert re.fullmatch(r"\d{10}", issuer["cik"])


def test_only_listed_issuer_symbols_can_be_earnings_targets():
    companies = {row["ticker"] for row in _companies()}
    for issuer in _issuers():
        if issuer["listed"]:
            assert issuer["tickers"]
            assert set(issuer["tickers"]) <= companies
        else:
            assert issuer["tickers"] == []
        if issuer.get("proposed_ticker"):
            assert not issuer["listed"]
            assert issuer["proposed_ticker"] not in companies


def test_private_model_labs_and_public_parent_are_distinct():
    issuers = {issuer["id"]: issuer for issuer in _issuers()}
    for issuer_id in ("anthropic", "openai", "xai", "databricks", "mistral",
                      "cohere", "together-ai", "groq"):
        assert issuers[issuer_id]["listed"] is False
    assert issuers["anthropic"]["proposed_ticker"] == "ANT"
    assert issuers["cerebras"]["listed"] is True
    assert issuers["cerebras"]["tickers"] == ["CBRS"]
    assert issuers["xai"]["parent_id"] == "spacex"
    assert issuers["xai"]["parent_tickers"] == issuers["spacex"]["tickers"] == ["SPCX"]
    assert {"nvidia", "amd", "broadcom", "microsoft", "amazon", "alphabet",
            "meta", "oracle", "tsmc", "coreweave"} <= set(issuers)

