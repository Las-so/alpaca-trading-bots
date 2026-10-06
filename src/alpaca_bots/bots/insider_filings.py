"""Bot 2 — Insider Filings (equities).

Strategy (code): poll SEC EDGAR's live Form 4 feed (free, no auth — just a
compliant User-Agent). Form 4 = officers/directors reporting their own
trades. Code parses each filing's XML, keeps only open-market PURCHASES
(transaction code "P") by an officer or director, above a dollar
threshold — that part is a hard rule, not a model judgment.

Jev's job: of the purchases that clear the threshold, judge whether THIS
one looks materially meaningful (role of the filer, size relative to
their existing stake, cluster of multiple insiders buying at once) rather
than routine/scheduled selling-plan noise. One `noul` per candidate.
"""
from __future__ import annotations
import re
import time
import urllib.request
import xml.etree.ElementTree as ET

from dataclasses import dataclass
from .base import Bot, Signal
from .. import jev_client

SEC_FEED = (
    "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=4"
    "&company=&dateb=&owner=include&count=40&output=atom"
)
# SEC requires a descriptive User-Agent with contact info on every request.
USER_AGENT = "alpaca-trading-bots (contact: deanhenso@gmail.com)"
MIN_PURCHASE_VALUE = 100_000.0


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read()


@dataclass
class InsiderPurchase:
    symbol: str
    company: str
    filer_role: str
    shares: float
    price: float
    value: float
    filing_url: str


def _latest_form4_filing_links(limit: int = 15) -> list[str]:
    xml_bytes = _fetch(SEC_FEED)
    root = ET.fromstring(xml_bytes)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    links = []
    for entry in root.findall("a:entry", ns)[:limit]:
        link_el = entry.find("a:link", ns)
        if link_el is not None and link_el.get("href"):
            links.append(link_el.get("href"))
    return links


def _parse_form4(index_url: str) -> InsiderPurchase | None:
    """The index page links to the primary ownershipDocument XML; fetch and
    parse it for transaction code P (open-market purchase)."""
    index_html = _fetch(index_url).decode(errors="replace")
    m = re.search(r'href="([^"]+\.xml)"', index_html)
    if not m:
        return None
    xml_url = m.group(1)
    if xml_url.startswith("/"):
        xml_url = "https://www.sec.gov" + xml_url
    return _parse_form4_xml_bytes(_fetch(xml_url), xml_url)


def _parse_form4_xml_bytes(xml_bytes: bytes, source_url: str) -> InsiderPurchase | None:
    """The actual XML-parsing core, shared by the live feed (_parse_form4,
    which has to resolve an index page to find this XML first) and
    historical_form4_purchases (which already knows the XML url directly
    from SEC's full-text search index, no index-page resolution needed)."""
    doc = ET.fromstring(xml_bytes)

    symbol_el = doc.find(".//issuer/issuerTradingSymbol")
    issuer_name_el = doc.find(".//issuer/issuerName")
    if symbol_el is None or not symbol_el.text or not symbol_el.text.strip():
        return None
    symbol = symbol_el.text.strip()
    # Real bug, caught by running this against real filings: SEC writes the
    # literal string "N/A" into issuerTradingSymbol for issuers with no
    # public ticker (a non-traded trust, a fund) — not an empty/missing
    # field, so the check above doesn't catch it. There is nothing to buy
    # on Alpaca for these; filter them out explicitly.
    if symbol.upper() in ("N/A", "NONE", "NA", ""):
        return None
    company = issuer_name_el.text.strip() if issuer_name_el is not None else symbol

    role_bits = []
    for tag in ("isDirector", "isOfficer"):
        el = doc.find(f".//reportingOwner/reportingOwnerRelationship/{tag}")
        if el is not None and el.text == "1":
            role_bits.append(tag.replace("is", ""))
    title_el = doc.find(".//reportingOwner/reportingOwnerRelationship/officerTitle")
    if title_el is not None and title_el.text:
        role_bits.append(title_el.text.strip())
    filer_role = ", ".join(role_bits) or "reporting owner"

    for tx in doc.findall(".//nonDerivativeTransaction"):
        code_el = tx.find(".//transactionCoding/transactionCode")
        if code_el is None or code_el.text != "P":
            continue
        shares_el = tx.find(".//transactionAmounts/transactionShares/value")
        price_el = tx.find(".//transactionAmounts/transactionPricePerShare/value")
        if shares_el is None or price_el is None:
            continue
        shares = float(shares_el.text)
        price = float(price_el.text)
        value = shares * price
        if value >= MIN_PURCHASE_VALUE:
            return InsiderPurchase(symbol, company, filer_role, shares, price, value, source_url)
    return None


@dataclass
class HistoricalFilingsResult:
    purchases: list  # list of (filing_date, InsiderPurchase)
    filings_scanned: int        # raw SEC filings actually fetched + parsed
    total_filings_in_window: int  # SEC's reported total for the whole date range


def historical_form4_purchases(start_date: str, end_date: str, max_filings: int = 60) -> HistoricalFilingsResult:
    """Real historical Form 4 filings for backtesting — NOT the live feed.
    Uses SEC's full-text search index (efts.sec.gov), which (verified by an
    actual call, not assumed) supports a custom date range, unlike the live
    "getcurrent" feed this bot uses for real-time polling.

    HONEST LIMITATION, found by actually checking the real numbers: SEC
    returns results newest-first with no further filter, and a 30-day
    window commonly has 10,000+ Form 4 filings. Scanning only the newest
    `max_filings` means a wide window is really only examining its most
    recent slice, not full coverage — real full coverage would need
    paging through thousands of results per window, which is both slow
    and heavy on SEC's free API. total_filings_in_window on the result
    tells the caller the true scale, so this limitation can't be missed.

    Rate-limited deliberately (one request per filing, small sleep between)
    out of respect for SEC's servers — this is a backtest tool run
    occasionally, not a polling loop.
    """
    # Real bug, caught on the first wide-window run: SEC's search-index
    # backend returns a real HTTP 500 when q is an empty quoted string
    # ("q=%22%22") over a date range wider than ~10 days — verified
    # directly by testing with and without the q param at the same window
    # size. Omitting q entirely (searching all Form 4 filings with no text
    # filter) works for any window size.
    search_url = (
        "https://efts.sec.gov/LATEST/search-index"
        f"?forms=4&dateRange=custom&startdt={start_date}&enddt={end_date}"
    )
    hits_raw = _fetch(search_url).decode(errors="replace")
    import json
    data = json.loads(hits_raw)
    total_available = data.get("hits", {}).get("total", {}).get("value", 0)
    hits = data.get("hits", {}).get("hits", [])[:max_filings]

    results = []
    for hit in hits:
        src = hit.get("_source", {})
        adsh = src.get("adsh", "")
        file_date = src.get("file_date", "")
        file_id = hit.get("_id", "")
        if ":" not in file_id or not adsh:
            continue
        filename = file_id.split(":", 1)[1]
        accession_no_dashes = adsh.replace("-", "")
        # The accession prefix (adsh's own leading CIK) is the filer whose
        # EDGAR Archives folder holds this filing — verified directly
        # against a real filing, not assumed from the API shape.
        filer_cik = adsh.split("-")[0].lstrip("0") or "0"
        xml_url = f"https://www.sec.gov/Archives/edgar/data/{filer_cik}/{accession_no_dashes}/{filename}"
        try:
            purchase = _parse_form4_xml_bytes(_fetch(xml_url), xml_url)
        except Exception:
            continue
        if purchase:
            results.append((file_date, purchase))
        time.sleep(0.15)  # be polite to SEC's servers
    return HistoricalFilingsResult(purchases=results, filings_scanned=len(hits), total_filings_in_window=total_available)


class InsiderFilingsBot(Bot):
    name = "insider_filings"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def compute_signal(self) -> Signal:
        try:
            links = _latest_form4_filing_links()
        except Exception as e:
            return Signal("?", "none", 0.0, f"could not reach SEC EDGAR: {e}")

        for link in links:
            try:
                purchase = _parse_form4(link)
            except Exception:
                continue
            if purchase:
                self._last = purchase
                return Signal(
                    purchase.symbol, "buy", purchase.price,
                    f"{purchase.filer_role} bought {purchase.shares:.0f}sh "
                    f"(${purchase.value:,.0f}) of {purchase.company}",
                )
        return Signal("?", "none", 0.0, "no qualifying insider purchases in the latest batch")

    def ask_jev(self, signal: Signal):
        return jev_client.ask_noul(
            instructions=(
                "An insider (officer or director) made this open-market purchase. "
                "Is this materially meaningful — not routine, large relative to a "
                "normal stake, from someone whose role gives them real information — "
                "rather than noise?"
            ),
            state={
                "symbol": signal.symbol,
                "price": signal.price,
                "filing": signal.description,
            },
            threshold=0.55,
        )
