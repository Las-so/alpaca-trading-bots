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
    doc = ET.fromstring(_fetch(xml_url))

    symbol_el = doc.find(".//issuer/issuerTradingSymbol")
    issuer_name_el = doc.find(".//issuer/issuerName")
    if symbol_el is None or not symbol_el.text:
        return None
    symbol = symbol_el.text.strip()
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
            return InsiderPurchase(symbol, company, filer_role, shares, price, value, index_url)
    return None


@dataclass
class InsiderFilingsBot(Bot):
    name: str = "insider_filings"

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
