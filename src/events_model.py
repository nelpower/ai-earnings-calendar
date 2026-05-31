"""Unified market-event model shared by every provider.

A single ``Event`` represents anything that can move the AI complex: an earnings
report, a macro release, an FOMC decision, a conference/keynote, an IPO, options
expiry, or an index rebalance. Providers all emit ``Event`` objects; the pipeline
merges them into one timeline.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

# Controlled category vocabulary (drives the site's colors/labels).
CATEGORIES = {
    "earnings": "财报",
    "macro": "宏观数据",
    "fed": "美联储/货币政策",
    "conference": "发布会 / 大会",
    "ipo": "IPO / 上市",
    "options": "期权到期",
    "index": "指数重构",
}

IMPORTANCE_ZH = {3: "高", 2: "中", 1: "低"}


@dataclass
class Event:
    date: str                       # ISO start date (YYYY-MM-DD)
    category: str                   # one of CATEGORIES
    title: str
    importance: int = 2             # 3 high / 2 medium / 1 low
    end_date: str = ""              # for multi-day events (conferences)
    time_et: str = ""               # "08:30", "盘后", "14:00", or ""
    tickers: list = field(default_factory=list)
    watch: str = ""                 # 你重点看什么
    source_url: str = ""
    meta: dict = field(default_factory=dict)   # category-specific extras

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Event":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})
