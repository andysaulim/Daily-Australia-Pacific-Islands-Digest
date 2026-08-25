"""
Google News URL canonicalizer.

Ported from the Middle East pipeline. Google News RSS links are opaque redirect
blobs of ~280 characters:
    https://news.google.com/rss/articles/CBMivgFBVV95cUxN...?oc=5

The first live run of this brief measured what that costs. Of 305 collected
items, 241 (79 percent) carried a Google News redirect. Their publish rate was
4.1 percent, against 39.1 percent for the 64 items with a canonical URL: a
9.5-fold gap on four fifths of the corpus. The Middle East pipeline diagnosed
the cause before this brief existed. The model cannot reproduce a 300-character
opaque string verbatim, so SOURCE-OR-SKIP treats real items as fabrications and
drops them.

Resolving the redirect fixes two things at once:
  1. Coverage. A short canonical URL is one the model can copy accurately, so
     items stop being discarded for a mechanical reason unrelated to their merit.
  2. Enrichment. fulltext.py deliberately skips Google News URLs, because
     fetching one returns an interstitial rather than article text. On the first
     run that left 241 of 305 items with only their feed blurb. Once resolved,
     they become fetchable.

Two decode paths, tried in order, both best-effort:
  A. Base64 path, for older article IDs that embed the target URL directly.
  B. Batch API path, for newer IDs: fetch the article page for its signature and
     timestamp, then ask Google's batchexecute endpoint to resolve the ID.

Every failure falls back to the original Google News URL, so resolution can only
improve the brief, never break it. Resolved URLs are cached in the archive
database, so retries and same-day duplicates cost nothing.

Stdlib only. Disable with RESOLVE_URLS=0.
"""

import base64
import json
import os
import re
import sqlite3
import time
import urllib.parse
import urllib.request
from pathlib import Path

import archive
DB_PATH = archive.DB_PATH   # honours the ARCHIVE_DB override the tests use

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                     "AppleWebKit/537.36 (KHTML, like Gecko) "
                     "Chrome/120.0 Safari/537.36"}
_BATCH_URL = "https://news.google.com/_/DotsSplashUi/data/batchexecute"

# Politeness / safety knobs.
ENABLED = os.environ.get("RESOLVE_URLS", "1") not in ("0", "false", "False", "")
TIME_BUDGET_S = float(os.environ.get("RESOLVE_TIME_BUDGET", "180"))  # stop resolving after this
PAUSE_S = 0.12          # brief sleep between live API calls, to be a good citizen
_ARTICLE_ID_RE = re.compile(r"/articles/([^/?]+)")
_SG_RE = re.compile(r'data-n-a-sg="([^"]+)"')
_TS_RE = re.compile(r'data-n-a-ts="([^"]+)"')
_HTTP_RE = re.compile(rb"https?://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+")


def is_gnews(url):
    return "news.google.com" in (url or "")


def _article_id(url):
    m = _ARTICLE_ID_RE.search(url or "")
    return m.group(1) if m else None


def _get(url, timeout=20):
    req = urllib.request.Request(url, headers=_UA)
    return urllib.request.urlopen(req, timeout=timeout).read()


def _decode_base64(article_id):
    """Older format: the target URL is embedded in the base64 article ID."""
    try:
        raw = base64.urlsafe_b64decode(article_id + "==")
    except Exception:
        return None
    m = _HTTP_RE.search(raw)
    if not m:
        return None
    url = m.group(0).decode("latin-1", "ignore")
    # The embedded string can carry trailing protobuf bytes; cut at the first control char.
    url = re.split(r"[\x00-\x1f]", url)[0]
    if url.startswith("http") and "news.google.com" not in url:
        return url
    return None


def _decode_api(article_id):
    """Newer format: fetch the article page for its signature + timestamp, then ask
    Google's batchexecute endpoint to resolve the ID to the publisher URL."""
    try:
        html = _get(f"https://news.google.com/rss/articles/{article_id}").decode(
            "utf-8", "ignore")
    except Exception:
        return None
    sg, ts = _SG_RE.search(html), _TS_RE.search(html)
    if not (sg and ts):
        return None
    inner = json.dumps([
        "garturlreq",
        [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1,
          None, None, None, None, None, 0, 1],
         "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0],
        article_id, int(ts.group(1)), sg.group(1),
    ])
    freq = json.dumps([[["Fbv4je", inner, None, "generic"]]])
    data = urllib.parse.urlencode({"f.req": freq}).encode()
    try:
        req = urllib.request.Request(
            _BATCH_URL, data=data,
            headers={**_UA,
                     "content-type": "application/x-www-form-urlencoded;charset=UTF-8"})
        resp = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "ignore")
        # Response is ")]}'\n\n<json>"; the payload we want is the second block.
        blocks = resp.split("\n\n")
        arr = json.loads(blocks[1] if len(blocks) > 1 else resp)
        url = json.loads(arr[0][2])[1]
        if url and url.startswith("http"):
            return url
    except Exception:
        return None
    return None


# --- cache ---------------------------------------------------------------

def _cache_init(con):
    con.execute("""CREATE TABLE IF NOT EXISTS url_cache (
                       gnews_url TEXT PRIMARY KEY, resolved_url TEXT )""")
    con.commit()


def resolve_url(article_id_or_url):
    """Resolve one Google News URL (or bare article id). Returns a canonical URL or None."""
    aid = article_id_or_url if "/" not in (article_id_or_url or "") \
        else _article_id(article_id_or_url)
    if not aid:
        return None
    return _decode_base64(aid) or _decode_api(aid)


def resolve_items(items):
    """Rewrite each item's `url` to its canonical publisher URL where possible.

    Only Google News redirect links are touched; direct-feed and manual URLs pass through.
    Keeps the original under `gnews_url`. Best-effort and time-bounded; anything not
    resolved keeps its working (if ugly) Google News link. Prints a one-line summary.
    """
    if not ENABLED:
        print("  [resolve] disabled (RESOLVE_URLS=0)")
        return items

    con = sqlite3.connect(DB_PATH)
    _cache_init(con)
    cache = dict(con.execute("SELECT gnews_url, resolved_url FROM url_cache").fetchall())

    start = time.monotonic()
    resolved = cached = failed = skipped = 0
    for it in items:
        url = it.get("url") or ""
        if not is_gnews(url):
            skipped += 1
            continue
        if url in cache:
            it["gnews_url"], it["url"] = url, cache[url]
            cached += 1
            continue
        if time.monotonic() - start > TIME_BUDGET_S:
            failed += 1
            continue
        canonical = resolve_url(url)
        time.sleep(PAUSE_S)
        if canonical:
            con.execute("INSERT OR REPLACE INTO url_cache VALUES (?,?)", (url, canonical))
            cache[url] = canonical
            it["gnews_url"], it["url"] = url, canonical
            resolved += 1
        else:
            failed += 1
    con.commit()
    con.close()

    took = time.monotonic() - start
    print(f"  [resolve] canonicalized {resolved} + {cached} cached, {failed} kept as "
          f"redirect, {skipped} already-direct ({took:.0f}s)")
    return items


# --- self-test (offline: base64 path + id parsing only) -------------------
if __name__ == "__main__":
    # An old-style ID with an embedded URL round-trips through the base64 path.
    embedded = "https://www.reuters.com/world/middle-east/example-story-2026-08-10/"
    payload = b"\x08\x13\x22" + bytes([len(embedded)]) + embedded.encode() + b"\xd2\x01\x00"
    fake_id = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    assert _decode_base64(fake_id) == embedded, _decode_base64(fake_id)

    fake_url = f"https://news.google.com/rss/articles/{fake_id}?oc=5"
    assert _article_id(fake_url) == fake_id
    assert is_gnews(fake_url) and not is_gnews(embedded)

    # A new-style ID (no embedded URL) must not yield a bogus base64 result.
    assert _decode_base64("CBMiAU_yqLNfakenewformatid") is None

    print("resolve.py self-test passed (base64 path)")
