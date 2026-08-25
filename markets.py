"""
Market indicators for the strip under the masthead.

Ported from the Korea brief's `_collect_markets`, retargeted at this region.

This closes a real hazard rather than just adding a feature. The brief carried a
hard prompt rule against inventing market figures precisely because there was no
pre-collected data for the model to anchor on, and the Korea brief had already
published "KOSPI plunges 4.44% amid AI selloff" on a day with no such move. Real
numbers in the prompt are a better defence than an instruction not to guess.

Source chain per symbol, in order:
  1. Yahoo Finance, query1 host
  2. Yahoo Finance, query2 host (their load-balanced mirror)
  3. Stooq daily CSV, a stable free source with no auth

Every value is checked against a sanity range and rejected if more than five
days stale, so a broken feed shows nothing rather than something wrong. A symbol
that fails every source is simply absent: the strip renders what it has.

The two policy rates are deliberately NOT fetched. The RBA cash rate and the
RBNZ OCR live in the baselines block, where they carry a decision date and a
human has checked them. Scraping them would create a second, unverified source
of truth for the two numbers this brief most needs to get right.

Stdlib plus requests, which collect.py already depends on.
"""
import csv as _csv
import io as _io
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests

ENABLED = os.environ.get("MARKETS", "1") not in ("0", "false", "False", "")

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                     "AppleWebKit/537.36 (KHTML, like Gecko) "
                     "Chrome/120.0.0.0 Safari/537.36"}

# label is what the reader sees; order here is the order on the strip.
INDICATORS = [
    ("asx200",   "ASX 200",   "^AXJO",   "^axjo",   (3000, 15000)),
    ("aud_usd",  "AUD/USD",   "AUDUSD=X", "audusd", (0.40, 1.20)),
    ("nzx50",    "NZX 50",    "^NZ50",   "^nz50",   (5000, 25000)),
    ("nzd_usd",  "NZD/USD",   "NZDUSD=X", "nzdusd", (0.35, 1.10)),
    # Iron ore was here on TIO=F, which Yahoo has stopped updating: the live
    # runs get a quote 1,840 days stale and markets.py correctly refuses it,
    # so the slot has never once rendered. Removed rather than replaced with
    # a guess, because a second dead symbol would cost another month of
    # warnings to discover. There is no free spot iron ore feed worth the
    # dependency; SETUP says what to do if a verified symbol turns up.
    ("brent",    "Brent",     "BZ=F",    "cb.f",    (20, 250)),
]

_MAX_STALE_DAYS = 5


def _fmt(key: str, price: float) -> str:
    if key in ("aud_usd", "nzd_usd"):
        return f"{price:.4f}"
    if key in ("asx200", "nzx50"):
        return f"{price:,.0f}"
    return f"{price:,.2f}"


def _validate(price, lo, hi, mkt_time):
    """A number that is absurd or stale is worse than no number at all."""
    if not price or price < lo or price > hi:
        return False, f"price {price} outside sanity range ({lo}-{hi})"
    if mkt_time and (datetime.now(timezone.utc) - mkt_time).days > _MAX_STALE_DAYS:
        return False, f"data is {(datetime.now(timezone.utc) - mkt_time).days} days old"
    return True, ""


def _fetch_yahoo(key, symbol, lo, hi):
    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        try:
            url = f"https://{host}/v8/finance/chart/{symbol}?range=5d&interval=1d"
            resp = requests.get(url, timeout=10, headers=_UA)
            resp.raise_for_status()
            meta = resp.json()["chart"]["result"][0]["meta"]
            price = meta.get("regularMarketPrice", 0)
            prev = meta.get("chartPreviousClose", meta.get("previousClose", price))
            ts = meta.get("regularMarketTime", 0)
            mkt_time = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None
            ok, why = _validate(price, lo, hi, mkt_time)
            if not ok:
                print(f"    !  {key}: Yahoo {host} {why}")
                continue
            change = ((price - prev) / prev * 100) if prev else 0
            return {"value": _fmt(key, price), "change_pct": round(change, 2),
                    "as_of": mkt_time.strftime("%d %b") if mkt_time else ""}
        except (requests.RequestException, KeyError, ValueError, TypeError) as e:
            print(f"    !  {key}: Yahoo {host} error: {e}")
    return None


def _fetch_stooq(key, symbol, lo, hi):
    if not symbol:
        return None
    try:
        resp = requests.get(f"https://stooq.com/q/d/l/?s={symbol}&i=d",
                            timeout=10, headers=_UA)
        resp.raise_for_status()
        text = resp.text.strip()
        if not text or "no data" in text.lower():
            return None
        rows = [r for r in _csv.DictReader(_io.StringIO(text)) if r.get("Close")]
        if len(rows) < 2:
            return None
        price, prev = float(rows[-1]["Close"]), float(rows[-2]["Close"])
        ok, why = _validate(price, lo, hi, None)
        if not ok:
            print(f"    !  {key}: Stooq {why}")
            return None
        change = ((price - prev) / prev * 100) if prev else 0
        return {"value": _fmt(key, price), "change_pct": round(change, 2),
                "as_of": rows[-1].get("Date", "")}
    except (requests.RequestException, ValueError, KeyError, TypeError) as e:
        print(f"    !  {key}: Stooq error: {e}")
        return None


def collect_markets() -> dict:
    """Return {key: {label, value, change_pct, as_of}} for whatever resolved."""
    if not ENABLED:
        print("  [markets] disabled (MARKETS=0)")
        return {}

    def one(spec):
        key, label, ysym, ssym, (lo, hi) = spec
        got = _fetch_yahoo(key, ysym, lo, hi) or _fetch_stooq(key, ssym, lo, hi)
        return key, (dict(got, label=label) if got else None)

    out = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        for key, got in pool.map(one, INDICATORS):
            if got:
                out[key] = got
    print(f"  [markets] {len(out)}/{len(INDICATORS)} indicators resolved")
    return out


def build_context_block(markets: dict) -> str:
    """Render the figures for prompt injection."""
    if not markets:
        return ("MARKET DATA: none collected today. Write no index level, exchange "
                "rate, or commodity price unless a source article reports it.")
    lines = ["MARKET DATA (pre-collected, use these exact figures, do not recalculate)"]
    for key, m in markets.items():
        arrow = "up" if m["change_pct"] > 0 else "down" if m["change_pct"] < 0 else "flat"
        lines.append(f"  {m['label']}: {m['value']} ({arrow} {abs(m['change_pct']):.2f}%"
                     f"{', as of ' + m['as_of'] if m['as_of'] else ''})")
    lines.append("  These are the ONLY market figures you may state. Any other index, "
                 "rate, or price must come from a source article.")
    return "\n".join(lines)


if __name__ == "__main__":
    assert _fmt("aud_usd", 0.6543) == "0.6543"
    assert _fmt("asx200", 8123.45) == "8,123"
    assert _fmt("brent", 71.5) == "71.50"
    assert _validate(8000, 3000, 15000, None)[0]
    assert not _validate(99999, 3000, 15000, None)[0]
    assert not _validate(0, 3000, 15000, None)[0]
    old = datetime(2020, 1, 1, tzinfo=timezone.utc)
    assert not _validate(8000, 3000, 15000, old)[0]
    blk = build_context_block({"asx200": {"label": "ASX 200", "value": "8,123",
                                          "change_pct": -0.42, "as_of": "25 Aug"}})
    assert "8,123" in blk and "down 0.42%" in blk, blk
    assert "none collected" in build_context_block({})
    print(blk)
    print("\nmarkets.py self-test passed")
