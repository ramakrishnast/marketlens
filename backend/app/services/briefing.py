"""Daily briefing: a factual, sourced morning summary for the user's watchlists.

1. `build_facts()` assembles every number from the app's own data (with links and dates).
2. With OPENAI_API_KEY, gpt-6-luna writes the prose strictly from those facts; otherwise a template renders them.
3. Saved with history; optionally emailed via SMTP.
No buy/sell recommendations.
"""
from __future__ import annotations

import json
import logging
import smtplib
from datetime import date, datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from .. import db
from ..config import get_settings
from . import congress, events, fundamentals, llm, macro, market, news, prices, signals, watchlists
from . import indicators as ind
from .universe import INDEX_STRIP, SECTOR_ETFS

log = logging.getLogger(__name__)


def build_facts() -> dict[str, Any]:
    today = date.today()
    tickers = watchlists.all_tickers()
    ov = market.overview()
    facts: dict[str, Any] = {"date": today.isoformat(), "watchlist_tickers": tickers}

    facts["indices"] = [{"ticker": x["ticker"], "label": x["label"], "close": x.get("price"), "change_pct": x.get("change_pct"),
                         "date": x.get("date"), "url": f"https://finance.yahoo.com/quote/{x['ticker']}"} for x in ov["indices"]]
    sect = sorted([s for s in ov["sectors"] if s.get("change_pct") is not None], key=lambda s: s["change_pct"], reverse=True)
    facts["sector_leaders"] = sect[:3]
    facts["sector_laggards"] = sect[-3:][::-1]
    try:
        m = macro.summary()["series"]
        facts["rates"] = [{k: r.get(k) for k in ("id", "label", "value", "date", "change_1m", "url")} for r in m if r["id"] in ("DGS10", "DGS2", "T10Y2Y", "DFF")]
    except Exception as e:
        log.info("macro unavailable for briefing: %s", e)
        facts["rates"] = []

    rows = market.watchlist_table(tickers) if tickers else []
    movers = sorted([r for r in rows if r.get("change_pct") is not None], key=lambda r: abs(r["change_pct"]), reverse=True)[:6]
    facts["movers"] = []
    for r in movers:
        hl = news.headlines(r["ticker"], days=3, limit=4)["items"]
        facts["movers"].append({"ticker": r["ticker"], "name": r.get("name"), "close": r["price"], "change_pct": r["change_pct"],
                                "volume_vs_avg": r.get("volume_vs_avg"), "date": r.get("date"),
                                "headlines": [{"headline": h["headline"], "source": h["source"], "url": h["url"], "published_at": h["published_at"]} for h in hl]})

    facts["new_signals"] = []
    for t in tickers:
        df = prices.daily(t, refresh_if_stale=False)
        if len(df) < 60:
            continue
        for e in signals.events(df, since_days=4, ix=ind.compute_all(df)):
            if e["id"] in ("macd_bull_cross", "macd_bear_cross"):
                continue
            facts["new_signals"].append({"ticker": t, **{k: e[k] for k in ("label", "rule", "date", "bias", "close")}})

    week_end = (today + timedelta(days=7)).isoformat()
    facts["earnings_this_week"] = []
    for t in tickers:
        try:
            ne = events.next_earnings(t)
        except Exception:
            ne = None
        if ne and today.isoformat() <= ne["date"] <= week_end:
            facts["earnings_this_week"].append({"ticker": t, **ne})
    facts["macro_events_this_week"] = [e for e in events.macro_events(7)]

    facts["new_filings"] = []
    since = (today - timedelta(days=3)).isoformat()
    for t in tickers:
        try:
            for f in fundamentals.filings(t, ("10-K", "10-Q", "8-K"), limit=10)["filings"]:
                if f["filing_date"] >= since:
                    facts["new_filings"].append({"ticker": t, "form": f["form"], "filing_date": f["filing_date"], "items": f["items"], "url": f["url"]})
        except Exception:
            continue

    facts["congress"] = []
    try:
        follow = set(congress.following()["members"])
        recent = congress.recent_filings(days=7)["filings"]
        for f in recent:
            for tr in f["trades"]:
                if f["member"] in follow or (tr.get("ticker") in tickers):
                    facts["congress"].append({"member": f["member"], "ticker": tr.get("ticker"), "tx_type": tr["tx_type"], "tx_date": tr["tx_date"],
                                              "disclosure_date": f["filing_date"], "amount": [tr["amount_low"], tr["amount_high"]], "url": f["url"]})
    except Exception as e:
        log.info("congress unavailable for briefing: %s", e)
    facts["data_as_of"] = {"prices": max((r.get("date") or "" for r in rows), default=None) or (ov["indices"][0].get("date") if ov["indices"] else None),
                           "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    return facts


def _f(v: float | None, d: int = 2, pct: bool = False) -> str:
    if v is None:
        return "n/a"
    return f"{v:+.{d}f}%" if pct else f"{v:,.{d}f}"


def render_template(f: dict[str, Any]) -> str:
    L = [f"# Market briefing: {f['date']}", f"_Data as of {f['data_as_of']['prices']} close. Factual summary from MarketLens data; not investment advice._", "", "## Market overview"]
    for x in f["indices"]:
        val = f"{_f(x['close'])}{'%' if x['ticker'] == '^TNX' else ''}"
        L.append(f"- **{x['label']}** ([{x['ticker']}]({x['url']})): {val} ({_f(x['change_pct'], pct=True)})")
    for r in f["rates"]:
        L.append(f"- {r['label']}: {_f(r['value'])} on {r['date']} (1-month change {_f(r.get('change_1m'))} pp) ([FRED]({r.get('url')}))")
    if f["sector_leaders"]:
        L.append("- Sector leaders: " + ", ".join(f"{s['ticker']} {s['name']} {_f(s['change_pct'], pct=True)}" for s in f["sector_leaders"]))
        L.append("- Sector laggards: " + ", ".join(f"{s['ticker']} {s['name']} {_f(s['change_pct'], pct=True)}" for s in f["sector_laggards"]))
    L += ["", "## Biggest movers in your watchlists"]
    if not f["movers"]:
        L.append("- Your watchlists are empty.")
    for m in f["movers"]:
        L.append(f"- **{m['ticker']}** {_f(m['change_pct'], pct=True)} to {_f(m['close'])}" + (f", volume {m['volume_vs_avg']:.1f}× average" if m.get("volume_vs_avg") else ""))
        for h in m["headlines"][:2]:
            L.append(f"  - [{h['headline']}]({h['url']}) ({h['source']})")
        if not m["headlines"]:
            L.append("  - No recent headlines found; the cause of the move is unclear from available news.")
    L += ["", "## New technical signals"]
    L += [f"- **{s['ticker']}**: {s['label']} on {s['date']} ({s['rule']})" for s in f["new_signals"]] or ["- None in the last few sessions."]
    L += ["", "## This week"]
    L += [f"- {e['date']}: **{e['ticker']}** earnings{' (estimated date)' if e.get('estimated') else ''}" for e in f["earnings_this_week"]]
    L += [f"- {e['date']}: {e['event']}" + (f" ([source]({e['url']}))" if e.get("url") else "") for e in f["macro_events_this_week"]]
    if not f["earnings_this_week"] and not f["macro_events_this_week"]:
        L.append("- No watchlist earnings or key macro events in the next 7 days.")
    L += ["", "## New filings"]
    L += [f"- **{x['ticker']}** [{x['form']}]({x['url']}) filed {x['filing_date']}" + (f" (items {x['items']})" if x["items"] else "") for x in f["new_filings"]] or ["- No new 10-K/10-Q/8-K in the last 3 days."]
    if f["congress"]:
        L += ["", "## Congress disclosures (followed members / your tickers)"]
        L += [f"- {c['member']}: {c['tx_type']} {c['ticker'] or 'non-stock asset'} on {c['tx_date']}, disclosed {c['disclosure_date']} ([filing]({c['url']}))" for c in f["congress"][:12]]
    return "\n".join(L)


AI_SYSTEM = ("You write a concise daily market briefing for one investor's watchlists. Use ONLY the JSON facts provided: every number, "
             "date and claim must come from them, and every news claim must link to its URL as a markdown link. If a mover has no "
             "headlines, say the cause is unclear. Neutral tone, no buy/sell recommendations, no predictions.")
AI_PROMPT = """Write the briefing in markdown with these sections:
# Market briefing: {date}
(one italic line: data as of ...; not investment advice)
## Market overview  (indexes, yields, VIX, sector leaders and laggards; 3-6 bullets)
## Biggest movers in your watchlists  (each mover: move, volume, and what the linked headlines say, with links)
## New technical signals
## This week  (earnings and macro events with dates)
## New filings
## Congress disclosures  (only if any)

FACTS (JSON):
{facts}"""


def generate(send_email: bool = True) -> dict[str, Any]:
    facts = build_facts()
    content, generator = None, "template"
    if llm.status()["enabled"]:
        try:
            content = llm.complete(AI_SYSTEM, AI_PROMPT.format(date=facts["date"], facts=json.dumps(facts, default=str)[:60000]), max_tokens=2500)
            generator = llm.model()
        except (llm.AIUnavailable, llm.AIError) as e:
            log.warning("AI briefing failed, using template: %s", e)
    if not content:
        content = render_template(facts)
    with db.tx() as c:
        cur = c.execute("INSERT INTO briefings(created_at, for_date, content_md, facts, generator, emailed) VALUES (?,?,?,?,?,0)",
                        (db.now_iso(), facts["date"], content, json.dumps(facts, default=str), generator))
        bid = cur.lastrowid
    emailed = False
    if send_email and get_settings().smtp_host and get_settings().briefing_email_to:
        try:
            send(content, facts["date"])
            emailed = True
            with db.tx() as c:
                c.execute("UPDATE briefings SET emailed=1 WHERE id=?", (bid,))
        except Exception as e:
            log.warning("briefing email failed: %s", e)
    return {"id": bid, "for_date": facts["date"], "generator": generator, "emailed": emailed}


def _md_to_html(md: str) -> str:
    import html
    import re

    out = []
    for line in md.splitlines():
        t = html.escape(line)
        t = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2">\1</a>', t)
        t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
        t = re.sub(r"(?<!\w)_(.+?)_(?!\w)", r"<i>\1</i>", t)
        if t.startswith("# "):
            out.append(f"<h2>{t[2:]}</h2>")
        elif t.startswith("## "):
            out.append(f"<h3>{t[3:]}</h3>")
        elif t.lstrip().startswith("- "):
            out.append(f"<div style='margin-left:{(len(t) - len(t.lstrip())) * 8 + 8}px'>• {t.lstrip()[2:]}</div>")
        elif t.strip():
            out.append(f"<p>{t}</p>")
    return "<div style='font-family:-apple-system,Segoe UI,sans-serif;font-size:14px;line-height:1.5'>" + "\n".join(out) + "</div>"


def send(content: str, for_date: str) -> None:
    s = get_settings()
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"MarketLens briefing: {for_date}"
    msg["From"] = s.smtp_from or s.smtp_user or "marketlens@localhost"
    msg["To"] = s.briefing_email_to
    msg.attach(MIMEText(content, "plain", "utf-8"))
    msg.attach(MIMEText(_md_to_html(content), "html", "utf-8"))
    with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=30) as smtp:
        smtp.ehlo()
        if s.smtp_port != 25:
            smtp.starttls()
            smtp.ehlo()
        if s.smtp_user and s.smtp_password:
            smtp.login(s.smtp_user, s.smtp_password)
        smtp.send_message(msg)


def history(limit: int = 60) -> list[dict[str, Any]]:
    return db.query("SELECT id, created_at, for_date, generator, emailed FROM briefings ORDER BY id DESC LIMIT ?", (limit,))


def get(bid: int | None = None) -> dict[str, Any] | None:
    row = db.query_one("SELECT * FROM briefings WHERE id=?", (bid,)) if bid else db.query_one("SELECT * FROM briefings ORDER BY id DESC LIMIT 1")
    if row:
        row["facts"] = json.loads(row["facts"])
    return row


def warm(_: list[str] | None = None) -> None:
    if not db.query_one("SELECT id FROM briefings WHERE for_date=?", (date.today().isoformat(),)):
        generate(send_email=False)


__all__ = ["generate", "history", "get", "build_facts", "render_template", "INDEX_STRIP", "SECTOR_ETFS"]
