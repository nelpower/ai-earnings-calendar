"""Render the unified AI market-event calendar (self-contained HTML, no JS/CDN)."""
from __future__ import annotations

import datetime as dt
import html
import json
from collections import Counter, defaultdict
from pathlib import Path

from src.config import SITE_DIR, today_local
from src.events_model import CATEGORIES, IMPORTANCE_ZH, Event

DISCLAIMER = (
    "数据来源:财报与预期来自 Yahoo Finance(yfinance);宏观/美联储日期来自 BLS/BEA/美联储官方"
    "发布日历;期权到期/指数重构为日历推算;发布会/政策监管/IPO 为人工整理或 Nasdaq 抓取。日期可能变动或为预估,"
    "财报/IPO 精确日期与盘前盘后请以公司 IR 或来源链接为准。本页仅供研究参考,不构成投资建议。"
)

_CAT_COLOR = {
    "earnings": "#4c8bf5", "macro": "#a371f7", "fed": "#e5534b",
    "conference": "#2bb0c2", "policy": "#e0708a", "ipo": "#2ea043",
    "options": "#d2992b", "index": "#8b949e", "industry": "#e8874a",
}
_WD = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

# Per-category label for the "entry" button (where to watch/read the result).
_ACTION_LABEL = {
    "earnings": "看财报 / 电话会",
    "macro": "看数据发布",
    "industry": "看数据发布",
    "fed": "看声明 / 发布会",
    "conference": "看直播 / 主题演讲",
    "policy": "看裁决 / 公告",
    "ipo": "看招股 / 行情",
    "options": "了解详情",
    "index": "了解详情",
}

_CSS = """
:root{--bg:#0f1115;--card:#1a1d24;--ink:#e8eaed;--muted:#9aa0aa;--line:#2a2e37;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;line-height:1.5;}
.wrap{max-width:920px;margin:0 auto;padding:0 16px 64px;}
header{position:sticky;top:0;background:rgba(15,17,21,.92);backdrop-filter:blur(8px);
border-bottom:1px solid var(--line);padding:14px 16px;z-index:9;}
header .wrap{padding:0;display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;}
h1{font-size:18px;margin:0;} .upd{color:var(--muted);font-size:12px;}
h2{font-size:16px;margin:26px 0 6px;}
.legend{display:flex;gap:6px;flex-wrap:wrap;margin:12px 0;}
.lg{font-size:11px;padding:2px 8px;border-radius:999px;border:1px solid var(--line);}
.dayhdr{font-size:13px;color:#cdd3dc;margin:18px 0 6px;font-weight:600;border-bottom:1px solid var(--line);padding-bottom:4px;}
.rel{color:#e9bd64;}
.card{background:var(--card);border:1px solid var(--line);border-left-width:4px;border-radius:10px;padding:11px 14px;margin:8px 0;}
.row{display:flex;align-items:center;gap:8px;flex-wrap:wrap;}
.catb{font-size:11px;padding:2px 8px;border-radius:999px;color:#0f1115;font-weight:700;}
.imp{font-size:10.5px;padding:1px 7px;border-radius:999px;border:1px solid var(--line);}
.imp3{background:rgba(229,83,75,.18);color:#ff8079;} .imp2{background:rgba(210,153,43,.16);color:#e9bd64;} .imp1{color:var(--muted);}
.title{font-weight:600;font-size:14.5px;}
.when{margin-left:auto;font-size:12px;color:var(--muted);}
.tk{font:600 11px/1 ui-monospace,monospace;background:#11141a;border:1px solid var(--line);color:#4c8bf5;padding:2px 6px;border-radius:6px;}
.watch{font-size:13px;color:#c9cfd8;margin-top:8px;} .watch b{color:#e8eaed;}
.ests{display:flex;gap:18px;margin-top:8px;flex-wrap:wrap;font-size:13px;}
.ests .k{font-size:11px;color:var(--muted);} .ests .v{font-weight:600;}
.lastq{font-size:12px;color:var(--muted);margin-top:7px;}
.bm{font-size:11px;padding:1px 7px;border-radius:999px;margin-left:4px;}
.beat{background:rgba(46,160,67,.15);color:#5fd97a;} .miss{background:rgba(229,83,75,.15);color:#ff8079;}
.tbd{font-size:10.5px;padding:1px 7px;border-radius:999px;border:1px dashed var(--line);color:#e9bd64;}
a{color:#4c8bf5;text-decoration:none;} a:hover{text-decoration:underline;}
.action{display:inline-block;font-size:12.5px;font-weight:700;color:#0f1115;padding:5px 12px;border-radius:7px;}
.action:hover{text-decoration:none;filter:brightness(1.1);}
.disc{background:#1a1410;border:1px solid #3a2a18;color:#e9c98f;border-radius:10px;padding:11px 14px;font-size:12.5px;margin:14px 0;}
footer{margin-top:30px;color:var(--muted);font-size:12px;border-top:1px solid var(--line);padding-top:14px;}
.empty{color:var(--muted);padding:8px 0;}
.lg[data-cat]{cursor:pointer;background:transparent;user-select:none;}
.lg.dim{opacity:.3;} .hide{display:none;}
.health{margin:16px 0;padding:10px 0;border-top:1px solid var(--line);border-bottom:1px solid var(--line);font-size:13px;}
.health p{margin:5px 0;color:#c9cfd8;}.health summary{cursor:pointer;font-weight:600;padding:7px 0;}
.health ul{padding-left:20px;}.health li{margin:7px 0;overflow-wrap:anywhere;}.health .warning{color:#e9c98f;}
"""

_FILTER_JS = """
var _flt=null;
function flt(cat){
  _flt=(_flt===cat)?null:cat;
  document.querySelectorAll('.lg[data-cat]').forEach(function(b){
    b.classList.toggle('dim', _flt!==null && b.dataset.cat!==_flt);});
  document.querySelectorAll('.card[data-cat]').forEach(function(c){
    c.classList.toggle('hide', _flt!==null && c.dataset.cat!==_flt);});
  document.querySelectorAll('.daygrp').forEach(function(g){
    g.classList.toggle('hide', !g.querySelector('.card:not(.hide)'));});
}
"""

# Yahoo revenue estimates arrive in the company's reporting currency.
_CUR_SYMBOL = {"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥", "CNY": "¥",
               "TWD": "NT$", "KRW": "₩", "HKD": "HK$"}


def _esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def _fmt_eps(v, cur: str = "") -> str:
    """EPS estimate. Yahoo's per-share figures for foreign reporters are not
    reliably USD, so when the reporting currency isn't USD we show a bare
    number rather than assert a wrong currency."""
    if v is None:
        return "—"
    if cur and cur != "USD":
        return f"{v:.2f}"
    return f"-${abs(v):.2f}" if v < 0 else f"${v:.2f}"


def _fmt_rev(v, cur: str = "") -> str:
    if v is None:
        return "—"
    cur = (cur or "USD").upper()
    sym = _CUR_SYMBOL.get(cur)
    pre = sym if sym else f"{cur} "
    if abs(v) >= 1e12:
        return f"{pre}{v/1e12:.2f}T"
    if abs(v) >= 1e9:
        return f"{pre}{v/1e9:.2f}B"
    if abs(v) >= 1e6:
        return f"{pre}{v/1e6:.0f}M"
    return f"{pre}{v:,.0f}"


def _rel(d: dt.date, today: dt.date) -> str:
    n = (d - today).days
    return {0: "今天", 1: "明天", 2: "后天"}.get(n, f"{n} 天后") if n >= 0 else "已过"


def _earnings_extra(meta: dict) -> str:
    eps, rev = meta.get("eps_estimate"), meta.get("revenue_estimate")
    cur = (meta.get("revenue_currency") or "").upper()
    block = (f'<div class="ests"><div><div class="k">EPS 预期</div>'
             f'<div class="v">{_fmt_eps(eps, cur)}</div></div>'
             f'<div><div class="k">营收 预期</div><div class="v">{_fmt_rev(rev, cur)}</div></div></div>')
    la, le = meta.get("last_eps_actual"), meta.get("last_eps_estimate")
    if la is not None and le is not None:
        cls = "beat" if la >= le else "miss"
        word = "超预期" if la >= le else "不及预期"
        # a percentage against a near-zero estimate is meaningless noise
        if abs(le) >= 0.10:
            pct = (la - le) / abs(le) * 100
            badge = f'<span class="bm {cls}">{word} {"+" if pct>=0 else ""}{pct:.1f}%</span>'
        else:
            badge = f'<span class="bm {cls}">{word}</span>'
        q = f"（{meta['last_quarter']}）" if meta.get("last_quarter") else ""
        block += (f'<div class="lastq">上季 EPS{q}：实际 <b style="color:#e8eaed">{_fmt_eps(la, cur)}</b>'
                  f' · 预期 {_fmt_eps(le, cur)}{badge}</div>')
    return block


def _when_label(e: Event) -> str:
    """'06/15–06/18 · 13:00 ET', '14:00 ET', '06/15–06/18', or '' — never a
    dangling 'ET'."""
    parts = []
    if e.end_date and e.end_date != e.date:
        parts.append(f"{e.date[5:].replace('-', '/')}–{e.end_date[5:].replace('-', '/')}")
    if e.time_et:
        parts.append(f"{e.time_et} ET")
    return " · ".join(parts)


def _card(e: Event, today: dt.date) -> str:
    color = _CAT_COLOR.get(e.category, "#8b949e")
    cat_zh = CATEGORIES.get(e.category, e.category)
    tickers = "".join(f'<span class="tk">{_esc(t)}</span>' for t in (e.tickers or []))
    ticker_row = f'<div class="row" style="margin-top:6px">{tickers}</div>' if tickers else ""
    when = _when_label(e)
    when_html = f'<span class="when">{_esc(when)}</span>' if when else ""
    tbd = (f'<span class="tbd">{_esc(e.meta.get("date_label") or "日期待确认")}</span>'
           if e.meta.get("date_confirmed") is False else "")
    if e.meta.get("date_source_stale"):
        tbd += f'<span class="tbd">官方日期待复核 · 沿用 {_esc(e.meta.get("date_last_verified") or e.meta.get("last_verified") or "上次")} 记录</span>'
    elif e.meta.get("source_stale"):
        tbd += f'<span class="tbd">来源暂不可用 · 沿用 {_esc(e.meta.get("last_verified") or "上次")} 记录</span>'
    if e.meta.get("estimates_stale"):
        tbd += f'<span class="tbd">预期数据沿用 {_esc(e.meta.get("estimates_last_verified") or "上次")} 记录</span>'
    if e.meta.get("status") == "cancelled":
        tbd = '<span class="tbd">已取消</span>'
    extra = _earnings_extra(e.meta) if e.category == "earnings" else ""
    label = _ACTION_LABEL.get(e.category, "查看")
    action = (f'<div class="row" style="margin-top:8px">'
              f'<a class="action" style="background:{color}" href="{_esc(e.source_url)}"'
              f' target="_blank" rel="noopener">▶ {label} ↗</a></div>'
              if e.source_url else "")
    return f"""<div class="card" data-cat="{_esc(e.category)}" style="border-left-color:{color}">
  <div class="row">
    <span class="catb" style="background:{color}">{_esc(cat_zh)}</span>
    <span class="imp imp{e.importance}">重要度 {IMPORTANCE_ZH.get(e.importance,'中')}</span>
    {tbd}
    <span class="title">{_esc(e.meta.get('display_title') or e.title)}</span>
    {when_html}
  </div>
  {ticker_row}
  {extra}
  <div class="watch"><b>看点:</b> {_esc(e.watch)}</div>
  {action}
</div>"""


def _timeline(events: list[Event], today: dt.date) -> str:
    if not events:
        return '<div class="empty">该区间暂无事件。</div>'
    by_day: dict[str, list[Event]] = defaultdict(list)
    for e in events:
        by_day[e.date].append(e)
    out = []
    for day in sorted(by_day):
        d = dt.date.fromisoformat(day)
        out.append(f'<div class="daygrp" id="day-{day}">')
        out.append(f'<div class="dayhdr">{d:%Y-%m-%d} {_WD[d.weekday()]} · '
                   f'<span class="rel">{_rel(d, today)}</span></div>')
        for e in sorted(by_day[day], key=lambda x: -x.importance):
            out.append(_card(e, today))
        out.append("</div>")
    return "\n".join(out)


def _schedule_updates(events: list[Event], today: dt.date) -> str:
    """Surface moved dates relevant to this week without adding calendar events."""
    notices = []
    for e in events:
        try:
            original = dt.date.fromisoformat(e.meta.get("original_proposed_date", ""))
        except (TypeError, ValueError):
            continue
        if not 0 <= (original - today).days <= 7 or original.isoformat() == e.date:
            continue
        latest = "最新请求日期" if e.meta.get("date_confirmed") is False else "最新日期"
        status = "，尚未确认" if e.meta.get("date_confirmed") is False else ""
        title = e.meta.get("display_title") or e.title
        notices.append(
            f'<div class="daygrp"><div class="card" data-cat="{_esc(e.category)}">'
            f'<div class="title">{_esc(title)}</div>'
            f'<div class="watch">原拟 {original:%m/%d}；{latest} '
            f'{_esc(e.date[5:].replace("-", "/"))}{status}。</div>'
            f'<a href="#day-{_esc(e.date)}">查看最新日程与官方来源</a></div></div>')
    if not notices:
        return ""
    return '<section aria-label="重要日程更新"><h2>重要日程更新</h2>' + "".join(notices) + '</section>'


def _safe_source_link(url: str, label: str) -> str:
    if not str(url).startswith(("https://", "http://")):
        return _esc(label)
    return f'<a href="{_esc(url)}" target="_blank" rel="noopener">{_esc(label)}</a>'


def _health_panel(health: dict | None, candidates: list[dict] | None) -> str:
    if not health:
        return ""
    pending = [c for c in (candidates or []) if c.get("review_status") == "needs_review"]
    failed = [r for r in health.get("sources", []) if r.get("status") in {"failed", "invalid_schema"}]
    parts = ['<section class="health" aria-label="采集与核验状态">']
    parts.append(f'<b>覆盖 {int(health.get("company_count", 0))} 家上市公司</b>'
                 f' · {int(health.get("issuer_count", 0))} 个重点发行人/品牌'
                 f' · {int(health.get("official_source_count", 0))} 个官方发现源')
    if failed:
        parts.append(f'<p class="warning">{len(failed)} 个来源分区暂不可用；'
                     f'{int(health.get("stale_events", 0))} 条日程含沿用数据，已标注上次核验日期。</p>')
    due = health.get("manual_review_due", [])
    conflicts = health.get("date_conflicts", [])
    for warning in health.get("warnings", []):
        if warning.get("kind") == "coverage_drop":
            parts.append(f'<p class="warning">{_esc(CATEGORIES.get(warning.get("category"), warning.get("category")))}日程由 '
                         f'{int(warning.get("before", 0))} 条降至 {int(warning.get("after", 0))} 条，请核查来源。</p>')
        elif warning.get("kind") == "macro_seed_expiring":
            parts.append(f'<p class="warning">宏观人工日程仅覆盖至 {_esc(warning.get("last_date"))}，后续月份待补充核验。</p>')
    if due or conflicts:
        parts.append(f'<p class="warning">人工日程待复核 {len(due)} 项 · 来源日期分歧 {len(conflicts)} 项。</p>')
    parts.append(f'<details><summary>查看来源状态与待核验线索（{len(pending)}）</summary>')
    names = {"earnings": "财报", "nasdaq_ipo": "IPO 定价", "nasdaq_lockup": "解禁", "official_discovery": "官方公告"}
    groups = Counter((r.get("source"), r.get("status")) for r in health.get("sources", []))
    for source in sorted({r.get("source", "") for r in health.get("sources", [])}):
        ok = groups[(source, "ok")] + groups[(source, "empty")]
        bad = groups[(source, "failed")] + groups[(source, "invalid_schema")]
        parts.append(f'<p>{_esc(names.get(source, source))}：成功读取 {ok} 个分区，异常 {bad} 个。</p>')
    if failed:
        parts.append('<ul>')
        for r in failed[:20]:
            parts.append(f'<li>{_esc(names.get(r.get("source"), r.get("source")))} / '
                         f'{_esc(r.get("partition"))}：暂不可用；上次成功 '
                         f'{_esc(r.get("last_success") or "暂无成功记录")}。</li>')
        parts.append('</ul>')
    if conflicts:
        parts.append('<p>来源日期存在分歧：</p><ul>')
        for c in conflicts[:10]:
            resolution = "采用官方活动日历" if c.get("resolution") == "official_calendar" else "待复核"
            parts.append(f'<li>{_safe_source_link(c.get("source_url", ""), c.get("title", ""))}：'
                         f'{_esc(c.get("current_date"))} / {_esc(c.get("other_date"))} · {resolution}</li>')
        parts.append('</ul>')
    if due:
        parts.append('<p>人工维护记录需要重新核验：</p><ul>')
        for item in due[:10]:
            parts.append(f'<li>{_safe_source_link(item.get("source_url", ""), item.get("title", ""))}'
                         f' · {_esc(item.get("date"))} · 上次核验 '
                         f'{_esc(item.get("last_verified") or "未记录")}</li>')
        parts.append('</ul>')
    if pending:
        parts.append('<p>以下是官方公告线索，尚未加入确定日程；公告发布日期不代表活动日期。</p><ul>')
        for item in pending[:20]:
            parts.append(f'<li>{_safe_source_link(item.get("url", ""), item.get("title", ""))}'
                         f' · 公告日期 {_esc(item.get("published_date") or "未标明")}</li>')
        parts.append('</ul>')
    else:
        parts.append('<p>本次未发现新的待核验线索；来源状态不代表已覆盖全部市场事件。</p>')
    parts.append('<p><a href="source_status.json">完整来源与复核报告</a> · '
                 '<a href="candidates.json">全部待核验线索</a></p></details></section>')
    return "\n".join(parts)


def build_html(this_week: list[Event], upcoming: list[Event],
               today: dt.date | None = None, health: dict | None = None,
               candidates: list[dict] | None = None) -> str:
    today = today or today_local()
    total = len(this_week) + len(upcoming)
    cats = Counter(e.category for e in this_week + upcoming)

    parts: list[str] = []
    A = parts.append
    A('<!doctype html><html lang="zh"><head><meta charset="utf-8">')
    A('<meta name="viewport" content="width=device-width,initial-scale=1">')
    A('<meta name="theme-color" content="#0f1115">')
    A("<title>AI 市场事件日历</title>")
    A(f"<style>{_CSS}</style></head><body>")
    A('<header><div class="wrap"><h1>🗓️ AI 市场事件日历</h1>'
      f'<span class="upd">更新于 {today} (北京时间) · 7 天内 {len(this_week)} 件 · 45 天内共 {total} 件</span>'
      "</div></header>")
    A('<div class="wrap">')

    # legend doubles as a category filter (click to isolate, click again to reset)
    A('<div class="legend">' + "".join(
        f'<span class="lg" data-cat="{c}" onclick="flt(\'{c}\')" role="button" '
        f'style="border-color:{_CAT_COLOR[c]};color:{_CAT_COLOR[c]}">'
        f'{CATEGORIES[c]} {cats.get(c,0)}</span>' for c in CATEGORIES if cats.get(c)
    ) + "</div>")

    A('<div class="upd">事件日期按来源当地日历；美股及美国事件为美东日期。预计日程与请求日期会单独标注。</div>')

    A(_schedule_updates(this_week + upcoming, today))
    A(_health_panel(health, candidates))

    A("<h2>本周大事（未来 7 天）</h2>")
    A(_timeline(this_week, today))

    A("<h2>未来 8–45 天</h2>")
    A(_timeline(upcoming, today))

    A(f'<footer><div class="disc">⚠️ {_esc(DISCLAIMER)}</div>'
      ' <a href="events.ics">📅 订阅日历 (ICS,手机日历可直接订阅)</a>'
      ' · <a href="events.json" download>下载数据 (JSON)</a></footer>')
    A(f"<script>{_FILTER_JS}</script>")
    A("</div></body></html>")
    return "\n".join(parts)


def build_site(this_week: list[Event], upcoming: list[Event],
               site_dir: Path = SITE_DIR, today: dt.date | None = None,
               health: dict | None = None, candidates: list[dict] | None = None) -> Path:
    site_dir = Path(site_dir)
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / "index.html").write_text(build_html(this_week, upcoming, today, health, candidates),
                                         encoding="utf-8")
    payload = {"updated": (today or today_local()).isoformat(),
               "this_week": [e.to_dict() for e in this_week],
               "upcoming": [e.to_dict() for e in upcoming]}
    (site_dir / "events.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return site_dir / "index.html"

