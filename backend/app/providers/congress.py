"""Congressional stock-trade disclosures (STOCK Act Periodic Transaction Reports).

House: the Clerk publishes a yearly index (XML in a zip) of all financial disclosures; PTRs
(FilingType "P") link to a PDF per filing. Electronically filed PTRs have a text layer we
parse; scanned paper filings (no text layer) are recorded as 'unparsed' and linked only.

Senate: TODO. The Senate eFD site needs an interactive terms-acceptance session; the free
community dataset stops in 2020. See DECISIONS.md.
"""
from __future__ import annotations

import io
import re
import time
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime

import httpx

from .base import CongressFiling, CongressTrade, ProviderError

HOUSE_BASE = "https://disclosures-clerk.house.gov/public_disc"
UA = {"User-Agent": "Mozilla/5.0 (MarketLens personal research)"}

_ROW = re.compile(
    r"^(?P<asset>.*?)\s*\[(?P<atype>[A-Z]{2,3})\]\s*"
    r"(?P<tx>P|S \(partial\)|S|E)\s+"
    r"(?P<td>\d{1,2}/\d{1,2}/\d{4})\s+(?P<nd>\d{1,2}/\d{1,2}/\d{4})\s+"
    r"(?P<amt>(?:Spouse/DC )?Over \$[\d,]+|\$[\d,]+(?:\.\d+)?(?:\s*-\s*\$[\d,]+(?:\.\d+)?)?)\s*$"
)
_META = re.compile(r"^(F S|S O|D|C|L|I V D|I P O|Description|Comments?|Subholding Of|Location|Filing Status)\s*:", re.I)
_TICKER = re.compile(r"\(([A-Z][A-Z0-9.\-]{0,6})\)")
_OWNER = re.compile(r"^(SP|JT|DC)\s+")


def _iso_date(s: str) -> str:
    return datetime.strptime(s, "%m/%d/%Y").date().isoformat()


def _amount(s: str) -> tuple[float | None, float | None]:
    nums = [float(x.replace(",", "")) for x in re.findall(r"\$([\d,]+(?:\.\d+)?)", s)]
    if not nums:
        return None, None
    if s.strip().lower().startswith(("over", "spouse")):
        return nums[0], None
    return nums[0], nums[-1]


def parse_ptr_text(text: str, filing: CongressFiling) -> list[CongressTrade]:
    """Parse the text layer of an electronic House PTR PDF into trades."""
    text = text.replace("\x00", "")
    trades: list[CongressTrade] = []
    buf: list[str] = []
    in_table = False
    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if not line:
            continue
        if line.startswith("$200?"):
            in_table, buf = True, []
            continue
        if not in_table:
            continue
        if line.startswith(("* For the complete list", "Filing ID #", "I CERTIFY", "Digitally Signed")):
            in_table = line.startswith("Filing ID #")  # a new page may continue the table
            buf = []
            continue
        if _META.match(line):
            buf = []
            continue
        buf.append(line)
        joined = " ".join(buf)
        if joined.endswith("-"):
            continue  # amount range wraps onto next line
        m = _ROW.match(joined)
        if not m:
            if len(buf) > 6:
                buf = buf[-3:]
            continue
        buf = []
        asset = m.group("asset").strip()
        owner = "self"
        om = _OWNER.match(asset)
        if om:
            owner = {"SP": "spouse", "JT": "joint", "DC": "dependent child"}[om.group(1)]
            asset = asset[om.end():]
        tickers = _TICKER.findall(asset)
        tx = m.group("tx")
        lo, hi = _amount(m.group("amt"))
        trades.append(CongressTrade(
            doc_id=filing.doc_id, chamber=filing.chamber, member=filing.member, state_dst=filing.state_dst,
            owner=owner, ticker=tickers[-1] if tickers else None, asset=asset, asset_type=m.group("atype"),
            tx_type={"P": "buy", "E": "exchange"}.get(tx, "sell"), tx_date=_iso_date(m.group("td")),
            disclosure_date=filing.filing_date, amount_low=lo, amount_high=hi, url=filing.url,
            extra={"partial": tx == "S (partial)", "notification_date": _iso_date(m.group("nd"))},
        ))
    return trades


class HouseClerkProvider:
    name = "house-clerk"

    def filings(self, year: int) -> list[CongressFiling]:
        r = httpx.get(f"{HOUSE_BASE}/financial-pdfs/{year}FD.zip", headers=UA, timeout=60, follow_redirects=True)
        if r.status_code != 200:
            raise ProviderError(f"House Clerk index HTTP {r.status_code}")
        zf = zipfile.ZipFile(io.BytesIO(r.content))
        xml_name = next(n for n in zf.namelist() if n.lower().endswith(".xml"))
        root = ET.fromstring(zf.read(xml_name))
        out = []
        for m in root:
            if m.findtext("FilingType") != "P":
                continue
            doc = m.findtext("DocID") or ""
            name = " ".join(x for x in [m.findtext("First"), m.findtext("Last"), m.findtext("Suffix")] if x)
            try:
                fdate = datetime.strptime(m.findtext("FilingDate") or "", "%m/%d/%Y").date().isoformat()
            except ValueError:
                continue
            out.append(CongressFiling(
                doc_id=doc, chamber="House", member=name.strip(), state_dst=m.findtext("StateDst") or "",
                filing_date=fdate, url=f"{HOUSE_BASE}/ptr-pdfs/{year}/{doc}.pdf",
            ))
        return out

    def trades(self, filing: CongressFiling) -> list[CongressTrade]:
        import pypdf

        for attempt in range(3):
            r = httpx.get(filing.url, headers=UA, timeout=60, follow_redirects=True)
            if r.status_code == 200:
                break
            time.sleep(2 * (attempt + 1))
        else:
            raise ProviderError(f"PTR PDF HTTP {r.status_code}")
        try:
            reader = pypdf.PdfReader(io.BytesIO(r.content))
            text = "\n".join((p.extract_text() or "") for p in reader.pages)
        except Exception as e:
            raise ProviderError(f"PDF parse failed: {e}")
        return parse_ptr_text(text, filing)
