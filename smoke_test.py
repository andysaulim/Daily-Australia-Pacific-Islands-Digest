"""Offline smoke test for the Australia Chair Daily Brief pipeline.

Exercises everything except the network and the Claude call: filters, region
tagging, trackers, the archive's cross-day memory, the validator, dedup, source
caps, URL repair, and the renderer.

    python smoke_test.py

Writes smoke_output.html so a layout change can be eyeballed without spending an
API call. Run this before every commit that touches the validator or renderer.
"""
import datetime
import inspect
import json
import os
import sys
import tempfile
from pathlib import Path

# Section 6 writes real rows through archive.py. Point the ledger at a throwaway
# file before archive is imported, so a test run cannot poison the cross-day
# memory that data/archive.db holds. Must precede the imports below.
_TMP_DB = Path(tempfile.gettempdir()) / "ausbrief_smoke_archive.db"
_TMP_DB.unlink(missing_ok=True)
os.environ["ARCHIVE_DB"] = str(_TMP_DB)

FAILS = []


def check(label, cond, detail=""):
    if cond:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label} {detail}")
        FAILS.append(label)


print("\n=== 1. Imports ===")
import collect, archive, aukus_tracker, pacific_tracker, calendar_tracker
import digest as digest_mod
import render as render_mod
import run as run_mod
import send_email
check("all modules import", True)
check("archive writes to the throwaway database, not data/archive.db",
      archive.DB_PATH == _TMP_DB, f"DB_PATH={archive.DB_PATH}")

print("\n=== 2. Regex filters ===")
check("AUSPAC matches an AUKUS headline",
      bool(collect.AUSPAC_KEYWORDS.search("Australia confirms AUKUS submarine milestone")))
check("AUSPAC matches a Solomons headline",
      bool(collect.AUSPAC_KEYWORDS.search("Solomon Islands signs policing deal")))
check("AUSPAC ignores unrelated 'Pacific Gas'",
      not collect.AUSPAC_KEYWORDS.search("Pacific Gas and Electric raises rates"))
check("PACIFIC tags Fiji", bool(collect.PACIFIC_KEYWORDS.search("Fiji PM visits Suva")))
check("NZ tags Wellington", bool(collect.NZ_KEYWORDS.search("Wellington reviews defence plan")))
check("sport block catches the Ashes",
      bool(collect._SPORT_FILTER.search("Australia wins the Ashes test match")))
check("sport block leaves policy alone",
      not collect._SPORT_FILTER.search("Australia lifts defence spending"))

print("\n=== 3. Region tagging ===")
class E(dict):
    def get(self, k, d=None): return dict.get(self, k, d)
art = collect._entry_to_article(E(title="Vanuatu signs port deal with China", link="http://x/1",
                                  summary="Port Vila agreement"), "RNZ Pacific")
check("Pacific article tagged Pacific", art["region"] == "Pacific", art["region"])
art2 = collect._entry_to_article(E(title="Luxon outlines NZ defence plan", link="http://x/2",
                                   summary="Wellington"), "RNZ National")
check("NZ article tagged NZ", art2["region"] == "NZ", art2["region"])
art3 = collect._entry_to_article(E(title="Canberra lifts defence budget", link="http://x/3",
                                   summary="Australia"), "ABC News")
check("AU article tagged AU", art3["region"] == "AU", art3["region"])

# Provenance fallback and content override. A Port Moresby story that never says
# "Papua New Guinea" must not fall through to Australia, and a Canberra story
# carried by a Pacific outlet must not be counted as Pacific. The Pacific count
# is what protects Pacific coverage, so both directions matter.
region_cases = [
    ("SIR JOHN NEEDS OUR SUPPORT", "", "PNG Post-Courier", "Pacific"),
    ("Government announces new funding round", "", "RNZ National", "NZ"),
    ("Canberra lifts defence budget", "", "RNZ Pacific", "AU"),
    ("Wellington reviews capability plan", "New Zealand", "The Australian", "NZ"),
    ("Australia funds new Fiji hospital wing", "Suva", "ABC News", "Pacific"),
    ("Local council debates rubbish collection", "", "ABC News", "AU"),
]
for title, summ, src, expect in region_cases:
    got = collect._entry_to_article(E(title=title, link="http://x", summary=summ), src)["region"]
    check(f"{src} / '{title[:32]}' -> {expect}", got == expect, f"got {got}")

print("\n=== 4. Trackers render context blocks ===")
for name, mod in (("aukus", aukus_tracker), ("pacific", pacific_tracker),
                  ("calendar", calendar_tracker)):
    block = mod.build_context_block()
    check(f"{name} tracker block non-empty", len(block) > 100, f"len={len(block)}")
check("aukus block flags unverified seeds", "UNVERIFIED SEED" in aukus_tracker.build_context_block())
check("calendar refuses to invent dates", "Do NOT generate a date from memory"
      in calendar_tracker.build_context_block())

print("\n=== 5. Calendar two-source gate ===")
try:
    calendar_tracker.add_event("Test event", "2026-09-01", ["http://a"])
    check("single-source date rejected", False, "no exception raised")
except ValueError:
    check("single-source date rejected", True)

print("\n=== 6. Archive round trip ===")
sample_articles = [
    {"url": "http://example.com/a1", "title": "Australia lifts defence spending",
     "source": "ABC News", "region": "AU", "summary": "x", "pub_date": ""},
    {"url": "http://example.com/a2", "title": "Fiji signs fisheries agreement",
     "source": "Islands Business", "region": "Pacific", "summary": "y", "pub_date": ""},
]
archive.store_items(sample_articles)
fake_digest = {
    "top_stories": [{"url": "http://example.com/a1",
                     "headline": "Australia lifts defence spending"}],
    "pacific_wire": [{"url": "http://example.com/a2",
                      "headline": "Fiji signs fisheries agreement"}],
}
n = archive.record_published(fake_digest)
check("record_published stored rows", n == 2, f"n={n}")
hit = archive.lookup_published("http://example.com/a1", "")
check("lookup finds the published URL", hit is not None and hit["match"] == "url")
hit2 = archive.lookup_published("http://other.com/z", "Australia lifts defence spending")
check("lookup matches on normalized headline",
      hit2 is not None and hit2["match"] == "headline")
block = archive.build_context_block(days=3)
check("ALREADY COVERED block builds", "ALREADY COVERED" in block)
stale = archive.is_stale_repeat({"url": "http://example.com/a1", "body_text": "Same as before."})
check("stale repeat detected", stale is not None)
fresh = archive.is_stale_repeat({"url": "http://example.com/a1",
                                 "body_text": "Canberra added $3 billion on Tuesday."})
check("repeat with a new figure is spared", fresh is None)

print("\n=== 7. Validator ===")
def base_digest():
    return {
        "re_line": "Canberra lifts defence spending; Fiji signs fisheries pact.",
        "morning_memo": ["Item one.", "Item two.", "Item three."],
        "top_stories": [
            {"url": "http://example.com/t1", "source": "ABC News", "category_tag": "AU-Defense",
             "headline": "Australia lifts defence spending",
             "body": "Canberra raised the defence budget by three billion dollars. " * 3},
            {"url": "http://example.com/t2", "source": "SMH", "category_tag": "Pacific-Diplomacy",
             "headline": "Fiji signs fisheries agreement",
             "body": "Suva concluded a regional fisheries arrangement this week. " * 3},
        ],
        "overnight_items": [
            {"url": f"http://example.com/o{i}", "source": "Reuters", "category": "AU-Politics",
             "headline": f"Overnight story {i}", "signal_type": "DEVELOPMENT",
             "body_text": "A substantive body of text about the day's developments. " * 3}
            for i in range(3)
        ],
        "pacific_wire": [
            {"url": "http://example.com/p1", "source": "RNZ Pacific", "country": "Fiji",
             "headline": "Fiji parliament debates budget",
             "body_text": "Fiji legislators opened debate on the national budget. " * 2},
            {"url": "http://example.com/p2", "source": "Islands Business", "country": "Vanuatu",
             "headline": "Vanuatu reviews port concession",
             "body_text": "Port Vila reopened the concession review this week. " * 2},
        ],
        "new_zealand": [
            {"url": "http://example.com/n1", "source": "RNZ National", "category": "NZ-Defense",
             "headline": "New Zealand reviews defence capability plan",
             "body_text": "Wellington published an updated capability schedule. " * 2},
        ],
        "calendar_watch": [
            {"date": None, "window": "expected in August", "event": f"Event {i}",
             "why_it_matters": "It shapes the regional agenda.", "confirmed": False}
            for i in range(4)
        ],
        "also_today": [], "opeds_today": [], "academic_today": [],
        "aukus_watch": [], "china_in_the_pacific": [], "canberra_politics": [],
        "business_economy": [], "primary_documents": [], "on_this_day": [],
    }

d = base_digest()
words = digest_mod._count_digest_words(d)
print(f"    (synthetic digest is {words} words)")
warnings = run_mod.validate_digest(d)
crit = [w for w in warnings if "CRITICAL" in w and "WORD COUNT" not in w]
check("clean digest has no structural CRITICAL", not crit, str(crit))

d2 = base_digest()
d2["pacific_wire"] = [{"stand_in": "No significant Pacific Islands developments in the past 24 hours."}]
w2 = run_mod.validate_digest(d2)
check("stand-in satisfies the Pacific floor",
      not [w for w in w2 if "PACIFIC WIRE CRITICAL" in w],
      str([w for w in w2 if "PACIFIC" in w]))
check("stand-in is surfaced as a non-blocking note",
      any("stand-in line used" in w for w in w2))

d3 = base_digest()
d3["pacific_wire"] = [
    {"url": "http://example.com/x", "source": "ABC News", "country": "Regional",
     "headline": "Canberra announces new tax settings",
     "body_text": "The treasurer outlined income tax thresholds."},
    {"url": "http://example.com/y", "source": "ABC News", "country": "Regional",
     "headline": "Sydney transport upgrade approved",
     "body_text": "The state government approved a rail extension."},
]
w3 = run_mod.validate_digest(d3)
check("padding the Pacific section is caught",
      any("looks like padding" in w for w in w3), str([w for w in w3 if "PACIFIC" in w]))

d4 = base_digest()
d4["top_stories"][0]["body"] = "Canberra raised spending — a first since 2009."
w4 = run_mod.validate_digest(d4)
check("em-dash is a CRITICAL failure", any("em-dash" in w for w in w4))

d5 = base_digest()
d5["re_line"] = "Defence budget rises \U0001F1E6\U0001F1FA"
w5 = run_mod.validate_digest(d5)
check("emoji is a CRITICAL failure", any("emoji" in w for w in w5))

d6 = base_digest()
d6["morning_memo"] = ["Same.", "Same.", "Other."]
w6 = run_mod.validate_digest(d6)
check("duplicate memo items caught", any("MORNING MEMO CRITICAL" in w for w in w6))

print("\n=== 8. Dedup and diversity ===")
d7 = base_digest()
d7["also_today"] = [{"url": "http://example.com/t1", "source": "AAP",
                     "headline": "Australia lifts defence spending again",
                     "body_text": "A second write-up of the same announcement."}]
d7, log = run_mod._dedup_digest(d7)
check("cross-section duplicate URL removed", len(d7["also_today"]) == 0, str(log))

d8 = base_digest()
d8["also_today"] = [{"url": f"http://example.com/w{i}", "source": "Reuters",
                     "headline": f"Wire item {i}", "body_text": "Body."} for i in range(5)]
div_log = run_mod._enforce_source_diversity(d8)
check("source cap of 3 enforced", len(d8["also_today"]) == 3, f"{len(d8['also_today'])} kept")

d9 = base_digest()
d9["pacific_wire"] = [{"url": f"http://example.com/pw{i}", "source": "RNZ Pacific",
                       "country": "Fiji", "headline": f"Fiji item {i}",
                       "body_text": "Body."} for i in range(4)]
run_mod._enforce_source_diversity(d9)
check("floored sections are exempt from the source cap",
      len(d9["pacific_wire"]) == 4, f"{len(d9['pacific_wire'])} kept")

print("\n=== 9. URL repair ===")
payload = {"tier1": [{"url": "http://real.example.com/story",
                      "title": "Australia lifts defence spending sharply"}],
           "tier2": [], "tier3": [], "tier4": []}
d10 = base_digest()
d10["top_stories"][0]["url"] = "#"
d10["top_stories"][0]["headline"] = "Australia lifts defence spending sharply"
repair_log = run_mod._repair_digest_urls(d10, payload)
check("placeholder URL repaired by headline match",
      d10["top_stories"][0]["url"] == "http://real.example.com/story", str(repair_log))

print("\n=== 10. Render ===")
html = render_mod.render(base_digest())
check("HTML produced", len(html) > 4000, f"{len(html)} bytes")
check("no em-dash in rendered output", "—" not in html)
check("teal accent present", "#17798C" in html)
check("masthead present", "Australia Chair Daily Brief" in html)
check("Pacific Wire section rendered", "Pacific Wire" in html)
check("New Zealand section rendered", ">New Zealand<" in html or "New Zealand" in html)
check("no unresolved f-string braces", "{_" not in html and "{TEAL" not in html)
check("signal badges are gone", "DEVELOPMENT" not in html and "ESCALATION" not in html
      and "CONFIRMATION" not in html)
check("category accents cut to three geographies",
      {render_mod._cat_color(c) for c in
       ("AUKUS", "AU-Defense", "Trade-Economy")} == {render_mod.NAVY}
      and render_mod._cat_color("China-Pacific") == render_mod.TEAL
      and render_mod._cat_color("NZ-Politics") == render_mod.NZ_GREEN)
check("unknown category falls back", render_mod._cat_color("Nonsense") == render_mod.NAVY)

html_si = render_mod.render(d2)
check("stand-in line renders", "No significant Pacific Islands developments" in html_si)

plain = send_email._html_to_plain_text(html)
check("plain-text alternative generated", len(plain) > 500, f"{len(plain)} chars")
check("plain text has no tags", "<div" not in plain and "<td" not in plain)

Path("smoke_output.html").write_text(html, encoding="utf-8")
print("    (wrote smoke_output.html)")

print("\n=== 11. Landing page ===")
idx = run_mod._build_index_html()
check("index builds", "Australia Chair Daily Brief" in idx and len(idx) > 800)

print("\n=== 11b. Survives a forward ===")
# Gmail and most clients strip <style> and <head> when a recipient forwards or
# replies. Simulate exactly that and confirm the fixed-width frame is still
# there: the width must be an HTML ATTRIBUTE, and section styling must be
# inline, or the layout sprawls full-width in the forwarded copy.
import re as _re_fwd
_stripped = _re_fwd.sub(r"<style[^>]*>.*?</style>", "", html, flags=_re_fwd.S | _re_fwd.I)
_stripped = _re_fwd.sub(r"<head[^>]*>.*?</head>", "", _stripped, flags=_re_fwd.S | _re_fwd.I)
check("no stylesheet element survives the strip",
      not _re_fwd.search(r"<style[\s>]", _stripped, _re_fwd.I))
check("width survives as an HTML attribute", 'width="680"' in _stripped)
check("wrapper is a table, not a max-width div",
      'class="wrapper"' in _stripped and "<table" in _stripped)
check("frame keeps an inline pixel width", "width:680px" in _stripped)
check("section padding is inline, not in a stripped class",
      "padding:20px 32px" in _stripped)
check("body copy keeps inline colour and size",
      "font-size:13px" in _stripped and "color:#555" in _stripped)
check("centring survives", 'align="center"' in _stripped)
# The mobile/dark-mode rules legitimately die on forward; assert they were the
# only casualties by confirming they lived in <style> and nowhere else.
check("media queries were style-only (acceptable loss)",
      "@media" in html and "@media" not in _stripped)

print("\n=== 11c. Reads on a phone ===")
# The frame carries width="680" so a forwarded copy keeps its shape. That same
# hard width would strand a forwarded copy at 680px on a 375px phone, where the
# media query that used to flex it no longer exists, so the inline ceiling has
# to be relative.
check("wrapper flexes below 680 on a narrow screen",
      "max-width:100%" in html and "max-width:680px" not in html)
check("mobile query still flexes the wrapper while the stylesheet lives",
      ".wrapper { width:100% !important; }" in html)

# The market strip was a single row of white-space:nowrap table cells: five or
# six indicators of unbreakable content, well past 375px, in a row that cannot
# wrap. That is a horizontal scrollbar on the whole email, not just the strip.
_mhtml_wrap = render_mod.render({
    "re_line": "x", "morning_memo": ["a", "b", "c"],
    "market_indicators": {k: {"label": k.upper(), "value": "1,000",
                              "change_pct": 0.5, "as_of": "25 Aug"}
                          for k in ("asx200", "aud", "nzx50", "nzd", "brent")}})
check("the market strip wraps instead of overflowing",
      'class="mkt"' in _mhtml_wrap and "display:inline-block" in _mhtml_wrap)
check("no nowrap table cell survives in the strip",
      '<td style="padding:0 14px 0 0;white-space:nowrap;">' not in _mhtml_wrap)
check("each indicator still holds its own figure on one line",
      _mhtml_wrap.count("white-space:nowrap") >= 5)

# The masthead is a two-column table. On a phone the right column has to stack,
# and on a forwarded phone copy, where no stylesheet stacks it, it has to be
# bounded by an attribute instead.
check("masthead meta column is stackable", 'class="hdr-meta"' in html)
check("masthead meta column is bounded without a stylesheet",
      'class="hdr-meta" width="130"' in html)
check("mobile query stacks the masthead",
      ".hdr-main, .hdr-meta" in html and "display:block !important" in html)

print("\n=== 12. Pipeline health monitor ===")
import pipeline_health

# The model IDs digest.py actually pins must be in the known-current set.
# This is the check that would have caught the Korea outage a quarter early.
check("FAST_MODEL is a known-current ID",
      digest_mod.FAST_MODEL in pipeline_health.KNOWN_MODEL_IDS,
      digest_mod.FAST_MODEL)
check("PRIMARY_MODEL is a known-current ID",
      digest_mod.PRIMARY_MODEL in pipeline_health.KNOWN_MODEL_IDS,
      digest_mod.PRIMARY_MODEL)

# The baselines block must carry a parseable "Verified as at" stamp, or
# staleness silently stops being tracked.
check("baselines carry a parseable verified date",
      pipeline_health._baseline_verified_date(digest_mod._REGIONAL_BASELINES) is not None)

# A starved payload must actually raise something, not pass quietly.
_starved = pipeline_health.check(
    payload={"tier1": [{"source": "Crikey"}], "tier2": [], "tier3": [], "tier4": []})
check("starved payload alerts on the prestige gap",
      any("prestige" in a for a in _starved["alerts"]))
check("starved payload warns on tier floors",
      any("tier1" in w for w in _starved["warnings"]))

_healthy = pipeline_health.check(
    payload={"tier1": [{"source": "ABC News"}] * 50 + [{"source": "SMH"}] * 10,
             "tier2": [{}] * 10, "tier3": [{}] * 3, "tier4": [{}] * 3})
check("healthy payload raises no alerts", not _healthy["alerts"], str(_healthy["alerts"]))

# The stream-retry tuple must catch what the SDK's backend actually raises.
# Catching the wrong HTTP library's classes is how this silently became dead
# code in the Korea and Japan pipelines.
try:
    import httpx2 as _h
except ImportError:
    import httpx as _h
check("stream retry catches a real backend protocol error",
      isinstance(_h.RemoteProtocolError("x"), digest_mod._STREAM_ERRORS))
check("stream retry catches a real backend stream error",
      isinstance(_h.StreamError("x"), digest_mod._STREAM_ERRORS))

check("market fabrication rule is in the system prompt",
      "MARKET AND RATE DATA" in digest_mod.SYSTEM_PROMPT)

print("\n=== 13. Full-text enrichment ===")
import fulltext

_html = ('<html><head><meta property="og:description" content="Meta sentence here.">'
         '</head><body><nav><p>Home</p></nav><article>'
         '<p>A real paragraph of article text carrying well over forty characters.</p>'
         '<p>Tiny.</p></article></body></html>')
check("meta description extracted", fulltext.extract_meta(_html) == "Meta sentence here.")
check("body drops nav and short fragments",
      "A real paragraph" in fulltext.extract_body(_html)
      and "Home" not in fulltext.extract_body(_html)
      and "Tiny." not in fulltext.extract_body(_html))
check("paywalled path takes meta only",
      "A real paragraph" not in fulltext.extract(_html, want_body=False))
check("google news URLs are recognised",
      fulltext.is_gnews("https://news.google.com/rss/search?q=x")
      and not fulltext.is_gnews("https://www.abc.net.au/news/x"))
check("regional paywalled outlets flagged",
      fulltext._is_paywalled("https://www.afr.com/x")
      and fulltext._is_paywalled("https://www.theaustralian.com.au/x")
      and not fulltext._is_paywalled("https://www.rnz.co.nz/x"))

# Exercise enrich() end to end with the network stubbed out, so the ranking,
# the Google News skip, the cache round trip and the summary append are all
# covered without a fetch.
_calls = []
def _fake_fetch(url, want_body, timeout=None):
    _calls.append((url, want_body))
    return f"BODY({'full' if want_body else 'meta'})"
fulltext._fetch = _fake_fetch

_items = [
    {"url": "https://news.google.com/rss/search?q=a", "source": "AAP", "summary": "skip me"},
    {"url": "https://www.abc.net.au/news/1", "source": "ABC News", "summary": "short"},
    {"url": "https://www.afr.com/2", "source": "AFR", "summary": "walled"},
]
fulltext.enrich(_items)
check("google news item left untouched", _items[0]["summary"] == "skip me")
check("canonical item enriched", "BODY(full)" in _items[1]["summary"]
      and _items[1]["summary"].startswith("short"))
check("paywalled item fetched meta-only", "BODY(meta)" in _items[2]["summary"])
check("only canonical URLs were fetched",
      len(_calls) == 2 and all("news.google.com" not in u for u, _ in _calls))

# Second pass must come from cache, issuing no further fetches.
_before = len(_calls)
_again = [{"url": "https://www.abc.net.au/news/1", "source": "ABC News", "summary": "short"}]
fulltext.enrich(_again)
check("second pass served from cache", len(_calls) == _before
      and "BODY(full)" in _again[0]["summary"])

# The digest must not truncate the enriched text back off again.
check("digest sends enriched summaries, not 800 chars",
      len(json.loads(digest_mod._tier_json(
          [{"title": "t", "url": "u", "summary": "x" * 1500,
            "source": "s", "region": "AU"}]))[0]["summary"]) == 1500)

print("\n=== 13b. Google News URL resolution ===")
import base64 as _b64
import resolve

_embedded = "https://www.abc.net.au/news/2026-08-25/aukus-payment-milestone/106572172"
_payload = b"\x08\x13\x22" + bytes([len(_embedded)]) + _embedded.encode() + b"\xd2\x01\x00"
_fake_id = _b64.urlsafe_b64encode(_payload).decode().rstrip("=")
_fake_url = f"https://news.google.com/rss/articles/{_fake_id}?oc=5"

check("base64 path decodes an embedded canonical URL",
      resolve._decode_base64(_fake_id) == _embedded, resolve._decode_base64(_fake_id))
check("article id parsed out of the redirect", resolve._article_id(_fake_url) == _fake_id)
check("gnews detection agrees with fulltext's",
      resolve.is_gnews(_fake_url) and not resolve.is_gnews(_embedded)
      and resolve.is_gnews(_fake_url) == fulltext.is_gnews(_fake_url))
check("new-style id yields nothing rather than a bogus URL",
      resolve._decode_base64("CBMiAU_yqLNfakenewformatid") is None)
check("resolver shares the archive database (honours ARCHIVE_DB)",
      resolve.DB_PATH == archive.DB_PATH, f"{resolve.DB_PATH}")

# End to end through resolve_items, offline: the base64 path needs no network,
# and a direct URL must pass through untouched.
_ritems = [{"url": _fake_url, "source": "ABC News"},
           {"url": "https://www.rnz.co.nz/news/pacific/story", "source": "RNZ Pacific"}]
resolve.resolve_items(_ritems)
check("redirect rewritten to the canonical URL", _ritems[0]["url"] == _embedded)
check("original redirect kept for reference", _ritems[0].get("gnews_url") == _fake_url)
check("direct URL untouched",
      _ritems[1]["url"] == "https://www.rnz.co.nz/news/pacific/story"
      and "gnews_url" not in _ritems[1])
check("resolution shortens the URL the model must copy",
      len(_ritems[0]["url"]) < len(_fake_url),
      f'{len(_ritems[0]["url"])} vs {len(_fake_url)}')

print("\n=== 13c. Prestige flagging ===")
_pf = collect._flag_prestige(collect._entry_to_article(
    E(title="Canberra lifts defence budget", link="http://x", summary="Australia"), "SMH"))
_nf = collect._flag_prestige(collect._entry_to_article(
    E(title="Canberra lifts defence budget", link="http://y", summary="Australia"), "Crikey"))
check("named outlet flagged", _pf.get("prestige_outlet") is True)
check("other outlet not flagged", "prestige_outlet" not in _nf)
check("flag reaches the model payload",
      json.loads(digest_mod._tier_json([_pf]))[0].get("prestige_outlet") is True)
check("prompt tells the model to use the flag",
      "prestige_outlet" in digest_mod.SYSTEM_PROMPT)

print("\n=== 14. Newsletter ingestion ===")
import newsletters

check("disabled by default, no IMAP attempt", newsletters.ENABLED is False)
check("returns nothing while disabled", newsletters.collect_newsletters() == [])

_nl_html = """
<a href="https://www.afr.com/politics/canberra-lifts-aukus-payment-20260825-p5abcd">
  Canberra lifts the next AUKUS industrial-base payment</a>
<a href="https://www.afr.com/topics/defence">Defence</a>
<a href="https://www.afr.com/sport/afl/grand-final-preview-20260825-p5xyz">
  Grand final preview: everything you need to know about the AFL decider</a>
<a href="https://example.com/unsubscribe">Unsubscribe from this newsletter</a>
<a href="https://www.rnz.co.nz/news/pacific/fiji-signs-security-arrangement-20260825">
  Fiji signs a new security arrangement with a regional partner</a>
"""
_nl = newsletters.parse_newsletter_html(_nl_html, "AFR (Newsletter)")
_nl_urls = " ".join(i["url"] for i in _nl)
check("newsletter article extracted", "canberra-lifts-aukus-payment" in _nl_urls)
check("sport link blocked in newsletters", "grand-final-preview" not in _nl_urls)
check("section page not treated as an article", "topics/defence" not in _nl_urls)
check("unsubscribe link dropped", "unsubscribe" not in _nl_urls)
check("newsletter items carry a region tag",
      all(i.get("region") in ("AU", "NZ", "Pacific") for i in _nl), str(_nl))
check("Pacific newsletter item tagged Pacific",
      any(i["region"] == "Pacific" for i in _nl if "fiji-signs" in i["url"]))
check("newsletter sender fingerprint matches",
      newsletters._match_source("news@afr.com", "Daily Briefing") == "AFR (Daily Briefing)"
      and newsletters._match_source("x@example.com", "hello") is None)

print("\n=== 14b. Market indicators ===")
import markets

check("value formatting by instrument type",
      markets._fmt("aud_usd", 0.65432) == "0.6543"
      and markets._fmt("asx200", 8123.45) == "8,123"
      and markets._fmt("brent", 71.5) == "71.50")
check("absurd price rejected", not markets._validate(99999, 3000, 15000, None)[0])
check("zero price rejected", not markets._validate(0, 3000, 15000, None)[0])
check("stale price rejected",
      not markets._validate(8000, 3000, 15000,
                            datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc))[0])
check("sane fresh price accepted", markets._validate(8000, 3000, 15000, None)[0])

_mk = {"asx200": {"label": "ASX 200", "value": "8,123", "change_pct": -0.42,
                  "as_of": "25 Aug"}}
_blk = markets.build_context_block(_mk)
check("context block carries the figure", "8,123" in _blk and "down 0.42%" in _blk)
check("context block forbids other figures", "ONLY market figures" in _blk)
check("empty case tells the model to write nothing", "none collected" in
      markets.build_context_block({}))
check("prompt injects the block",
      "MARKET DATA (pre-collected" in digest_mod.build_user_prompt(
          {"tier1": [], "region_counts": {}, "market_indicators": _mk}, "25 August 2026"))

_mhtml = render_mod.render({"re_line": "x", "morning_memo": ["a", "b", "c"],
                            "market_indicators": _mk})
check("strip renders the indicator", "ASX 200" in _mhtml and "8,123" in _mhtml)
check("a fall renders red", render_mod.ALERT in _mhtml)
check("strip absent when nothing resolved",
      "ASX 200" not in render_mod.render({"re_line": "x",
                                          "morning_memo": ["a", "b", "c"]}))

print("\n=== 14c. Section renames ===")
_rsrc = inspect.getsource(render_mod)
check("Canberra label is self-explanatory",
      '_sec_label("Canberra Politics")' in _rsrc
      and '_sec_label("Canberra")' not in _rsrc)
check("The Wire renamed to Also Today",
      '_sec_label("Also Today")' in _rsrc
      and '_sec_label("The Wire")' not in _rsrc)

print("\n=== 14d. Week in Review ===")
import weekly

_wsrc = inspect.getsource(weekly.load_week)
check("weekly reads the archive, not disk files",
      "FROM published" in _wsrc and "Path(" not in _wsrc
      and ".json" not in _wsrc)
_w = {"week_label": "25 to 29 August 2026", "re_line": "Week line.",
      "top_stories": [{"rank": 1, "headline": "Forum meets in Koror",
                       "body": "Body. " * 5, "category": "Pacific-Diplomacy",
                       "sources": ["Islands Business"], "url": "https://x/y"}],
      "pacific_thread": "Pacific text.", "nz_thread": None, "aukus_thread": None,
      "patterns": ["Theme one."], "bottom_line": "The bottom line."}
_wh = weekly.render_weekly(_w)
check("weekly renders its masthead", "Week in Review" in _wh)
check("weekly renders a story", "Koror" in _wh)
check("null threads are omitted",
      "Pacific Thread" in _wh and "New Zealand Thread" not in _wh)
check("weekly inherits the forwarding-safe frame", 'width="680"' in _wh)
check("weekly is em-dash clean", "—" not in _wh)
check("weekly shares the daily shell", "_shell" in inspect.getsource(weekly.render_weekly))

print("\n=== 14e. Cost tracking ===")
import cost_report

check("opus priced correctly", cost_report.cost_of("claude-opus-5", 0, 1_000_000) == 25.00)
check("sonnet priced correctly", cost_report.cost_of("claude-sonnet-5", 1_000_000, 0) == 2.00)
check("cache reads are a tenth of input",
      abs(cost_report.cost_of("claude-opus-5", cache_read=1_000_000) - 0.50) < 1e-9)
check("unknown model costs nothing rather than guessing",
      cost_report.cost_of("not-a-model", 1000, 1000) == 0.0)
check("digest keeps a token ledger", hasattr(digest_mod, "TOKEN_LEDGER"))
check("streamer records into the ledger",
      "TOKEN_LEDGER.append" in inspect.getsource(digest_mod._stream_claude))
check("run.py writes the ledger into metrics",
      '"tokens"' in inspect.getsource(run_mod.main))

print("\n=== 14f. Twelve-topic coverage ===")
# The mandate is twelve named topics. These checks keep the prompt, the render
# taxonomy, the archive and the health monitor from drifting apart, which is
# the only way "are we covering all twelve" can be answered mechanically.
_TOPICS = archive.COVERAGE_TOPICS
check("archive tracks exactly twelve topics", len(_TOPICS) == 12, str(len(_TOPICS)))
check("every topic is a renderable category",
      all(c in render_mod._CAT_COLORS for c in _TOPICS),
      str([c for c in _TOPICS if c not in render_mod._CAT_COLORS]))
check("every topic appears in the prompt checklist",
      all(c in digest_mod.SYSTEM_PROMPT for c in _TOPICS),
      str([c for c in _TOPICS if c not in digest_mod.SYSTEM_PROMPT]))
check("the twelve are named as the mandate",
      "THE CATEGORIES ARE THE MANDATE" in digest_mod.SYSTEM_PROMPT)
check("a topic is a mandate across the week, not a per-issue quota",
      "not a quota per issue" in digest_mod.SYSTEM_PROMPT)

# Category capture, including the section fallback for sections that carry none.
check("item category preferred over the section",
      archive.item_category({"category": "NZ-Defense"}, "pacific_wire") == "NZ-Defense")
check("category_tag also read",
      archive.item_category({"category_tag": "AUKUS"}, "also_today") == "AUKUS")
check("section implies a topic when the item gives none",
      archive.item_category({}, "china_in_the_pacific") == "China-Pacific")
check("no category and no implication yields nothing, not a guess",
      archive.item_category({}, "also_today") == "")

# Round trip: record a digest and read the coverage back out.
_cov_digest = {
    "top_stories": [{"url": "http://c/1", "headline": "A", "category_tag": "US-Australia"}],
    "new_zealand": [{"url": "http://c/2", "headline": "B", "category": "NZ-Defense"}],
    "aukus_watch": [{"url": "http://c/3", "headline": "C"}],
}
archive.record_published(_cov_digest, digest_date=archive._today())
_cov = archive.topics_covered(days=14)
check("coverage reports all twelve keys", len(_cov) == 12)
check("explicit category counted", _cov.get("US-Australia", 0) >= 1)
check("section-implied category counted", _cov.get("AUKUS", 0) >= 1)
check("an untouched topic reads zero, not missing",
      _cov.get("US-China-Pacific", None) == 0)

check("health monitor reports which topics are dark",
      "topics_dark" in pipeline_health.check(payload=None))
check("health monitor stays quiet with no categorized history",
      isinstance(pipeline_health.check(payload=None)["topics_dark"], list))

print("\n=== 14g. Section caps agree with the schema the model is given ===")
# The caps live in two places: SECTION_CAPS, which the validator enforces, and
# the prose schema in the user prompt, which the model reads. They drifted once
# already. A model told "maximum 5" while the validator allows 8 simply never
# uses the headroom, and nothing reports it.
_prompt = digest_mod.build_user_prompt({"tier1": [], "region_counts": {}}, "25 August 2026")
_cap_patterns = {
    "overnight_items":      r"- overnight_items: (\d+)-(\d+) items",
    "pacific_wire":         r"- pacific_wire: MINIMUM (\d+), maximum (\d+)",
    "new_zealand":          r"- new_zealand: MINIMUM (\d+), maximum (\d+)",
    "china_in_the_pacific": r"- china_in_the_pacific: (\d+)-(\d+) items",
    "also_today":           r"- also_today: (\d+)-(\d+) items",
    "aukus_watch":          r"- aukus_watch: (\d+)-(\d+) items",
    "canberra_politics":    r"- canberra_politics: (\d+)-(\d+) items",
    "business_economy":     r"- business_economy: (\d+)-(\d+) items",
}
import re as _re_caps
for _sec, _pat in _cap_patterns.items():
    _m = _re_caps.search(_pat, _prompt)
    if not _m:
        check(f"{_sec} cap stated in the schema", False, "pattern not found")
        continue
    _want = run_mod.SECTION_CAPS[_sec]
    _got = (int(_m.group(1)), int(_m.group(2)))
    check(f"{_sec} cap matches the validator", _got == _want, f"prompt {_got} vs caps {_want}")

check("pacific_wire has room for a meaningful share of the 17 states",
      run_mod.SECTION_CAPS["pacific_wire"][1] >= 12)

# Headroom without a dominance cap would just buy more Fiji. These two run
# together or the extra slots do not widen coverage at all.
_cd = {"pacific_wire": [
    {"headline": "Fiji 1", "country": "Fiji", "source": "a"},
    {"headline": "Fiji 2", "country": "Fiji", "source": "b"},
    {"headline": "Fiji 3", "country": "Fiji", "source": "c"},
    {"headline": "Fiji 4", "country": "Fiji", "source": "d"},
    {"headline": "PNG 1", "country": "PNG", "source": "e"},
    {"headline": "Reg 1", "country": "Regional", "source": "f"},
    {"headline": "Reg 2", "country": "Regional", "source": "g"},
    {"headline": "Reg 3", "country": "Regional", "source": "h"},
    {"headline": "Reg 4", "country": "Regional", "source": "i"},
]}
_cd_log = run_mod._enforce_country_diversity(_cd)
_cd_kept = [i["headline"] for i in _cd["pacific_wire"]]
check("a fourth item on one state is dropped",
      "Fiji 4" not in _cd_kept and len(_cd_log) == 1)
check("three on one state is allowed", _cd_kept.count("Fiji 1") == 1
      and "Fiji 3" in _cd_kept)
check("regional items are exempt from the country cap",
      len([h for h in _cd_kept if h.startswith("Reg")]) == 4)
check("another state is untouched", "PNG 1" in _cd_kept)
check("country aliases fold together",
      run_mod._normalize_country("PNG") == run_mod._normalize_country("Papua New Guinea")
      and run_mod._normalize_country("FSM") == "micronesia (fsm)"
      and run_mod._normalize_country("East Timor") == "timor-leste")
check("regional and blank normalise to no country",
      run_mod._normalize_country("Regional") == ""
      and run_mod._normalize_country("") == "")
check("the country cap runs in post-processing",
      "_enforce_country_diversity" in inspect.getsource(run_mod._postprocess_digest))
check("a stand-in is never country-capped",
      run_mod._enforce_country_diversity(
          {"pacific_wire": [{"stand_in": "No significant developments."}]}) == [])
check("the prompt tells the model the country cap exists",
      "more than 3 slots" in digest_mod.SYSTEM_PROMPT)
check("the prompt states the Regional exemption",
      "exempt from the cap" in digest_mod.SYSTEM_PROMPT)
check("Pacific ceiling exceeds the Canberra one",
      run_mod.SECTION_CAPS["pacific_wire"][1] > run_mod.SECTION_CAPS["canberra_politics"][1])

print("\n=== 14h. The corpus is ordered before it is cut ===")
# 299 tier-1 articles were collected on 25 August and 90 reached the model,
# taken off the front of the list in feed-completion order. Six wire services
# were collected and never shown; the sections filled with a consumer-law suit
# and a supermarket promotion. Ordering the corpus is what makes the cut honest.
_plain = {"title": "Council debates parking levy", "summary": "x" * 50,
          "region": "AU", "source": "news.com.au"}
_prest = {"title": "Council debates parking levy", "summary": "x" * 50,
          "region": "AU", "source": "Reuters", "prestige_outlet": True}
_pac = {"title": "Council debates parking levy", "summary": "x" * 50,
        "region": "Pacific", "source": "Fiji Times"}
_seen = dict(_plain, seen_before=True)
_mandate = {"title": "AUKUS submarine milestone slips at Osborne",
            "summary": "x" * 50, "region": "AU", "source": "news.com.au"}
check("a prestige outlet outranks a plain one",
      digest_mod._relevance_score(_prest) > digest_mod._relevance_score(_plain))
check("Pacific copy outranks Australian copy, all else equal",
      digest_mod._relevance_score(_pac) > digest_mod._relevance_score(_plain))
check("mandate vocabulary lifts an item",
      digest_mod._relevance_score(_mandate) > digest_mod._relevance_score(_plain))
check("a story already published is demoted",
      digest_mod._relevance_score(_seen) < digest_mod._relevance_score(_plain))
check("prioritisation is order, not filtering",
      len(digest_mod._prioritize([_plain, _prest, _seen, _pac])) == 4)
check("the highest scorer sorts first",
      digest_mod._prioritize([_plain, _seen, _prest])[0] is _prest)
check("ties keep collection order",
      digest_mod._prioritize([_plain, dict(_plain)])[0] is _plain)
_dsrc = inspect.getsource(digest_mod)
check("the tier JSON orders before it slices",
      "_prioritize(articles)[:max_items]" in _dsrc)
check("the tier-1 window is wide enough to matter",
      'payload.get("tier1", []), max_items=160' in _dsrc)
# A prestige item buried at position 200 of the raw list must still make the cut.
_bulk = [dict(_plain, title=f"filler {i}") for i in range(250)]
_win = digest_mod._prioritize(_bulk + [_prest])[:160]
check("a prestige item deep in the corpus survives the cut", _prest in _win)

print("\n=== 14i. Length targets match a twelve-topic beat ===")
check("pre-validation floor raised to 1,600",
      digest_mod._check_content_minimums(
          {"top_stories": [1, 2], "overnight_items": [1, 2, 3],
           "morning_memo": [1, 2, 3], "re_line": "x"})
      == ["word count 4 is below the 1600-word minimum"])
check("the prompt targets the 2,000-2,500 sent length",
      "between 2,000 and 2,500 words" in _dsrc and "2,300-2,800" in _dsrc)
check("the prompt keeps the 1,600 hard minimum",
      "HARD MINIMUM 1,600" in _dsrc)
check("the prompt names an upper bound too",
      "Do NOT exceed 2,800" in _dsrc)
check("the prompt sends a short draft to the Pacific first",
      "add items to pacific_wire" in _dsrc)
_rsrc_run = inspect.getsource(run_mod)
check("the send gate blocks below 1,400",
      "word_count < 1400" in _rsrc_run and "hard minimum 1400" in _rsrc_run)
check("the send gate warns below 2,000",
      "word_count < 2000" in _rsrc_run and "target 2000-2500" in _rsrc_run)
check("the send gate warns above 2,500 as well",
      "word_count > 2500" in _rsrc_run and "over the 2500 ceiling" in _rsrc_run)
check("no inherited Korea floor survives",
      "hard minimum 850" not in _rsrc_run and "1000-word minimum" not in _dsrc)

print("\n=== 14j. Relevance gate and coverage gaps ===")
check("the relevance gate is in the system prompt",
      "OFF-BEAT NEWS, THE RELEVANCE GATE" in digest_mod.SYSTEM_PROMPT)
for _term in ("Consumer protection", "Supermarket promotions",
              "Tourism marketing", "Recreational boating",
              "United States domestic politics"):
    check(f"the gate names {_term.lower()}", _term in digest_mod.SYSTEM_PROMPT)
check("the gate states that a short section beats a padded one",
      "worse failure than leaving it short" in digest_mod.SYSTEM_PROMPT)

# Deterministic: the suite runs against a temp archive, so publish one known
# item and read the gaps back rather than asserting against whatever the live
# database happens to hold.
import archive as _arch_gap
_arch_gap.record_published({"aukus_watch": [
    {"url": "http://example.com/gap", "headline": "Marape on AUKUS",
     "category": "AUKUS", "country": "Papua New Guinea"}]})
_gapblk = _arch_gap.build_coverage_gap_block(days=14)
check("a topic just published is not listed as a gap",
      "AUKUS" not in _gapblk.split("Pacific states")[0])
check("a topic with nothing published is listed by its reader-facing name",
      "Australian defence policy" in _gapblk)
check("a state just published is not listed as a gap",
      "Papua New Guinea" not in _gapblk)
check("a state with nothing published is listed", "Kiribati" in _gapblk)
check("the gap block is a tie-breaker, never a quota",
      "NOT a quota" in _gapblk and "do not invent one" in _gapblk)
check("the gap block is wired into the prompt",
      "COVERAGE GAPS TO CLOSE IF TODAY ALLOWS" in _dsrc
      and "build_coverage_gap_block(days=14)" in _dsrc)

print("\n=== 14k. Dead sources removed, broken ones rerouted ===")
check("the stale iron ore ticker is no longer queried",
      not any("TIO=F" in str(i) for i in markets.INDICATORS))
check("the remaining indicators are the ones that resolve",
      [i[1] for i in markets.INDICATORS]
      == ["ASX 200", "AUD/USD", "NZX 50", "NZD/USD", "Brent"])
check("the 500-ing ABC politics feed is rerouted, not deleted",
      "ABC Politics" in collect.TIER1_FEEDS
      and "news.google.com" in collect.TIER1_FEEDS["ABC Politics"]
      and "56166" not in collect.TIER1_FEEDS["ABC Politics"])

print("\n=== 14l. Dropped prestige stories are named ===")
_pw = run_mod.validate_digest(
    base_digest(),
    payload={"tier1": [{"source": "Reuters", "url": "http://example.com/wire",
                        "title": "Marles meets Austin on submarine timetable"}]})
_pline = [w for w in _pw if w.startswith("PRESTIGE")]
check("the warning fires on a dropped wire story", len(_pline) == 1)
check("the warning names the story, not just the outlet",
      "Marles meets Austin" in _pline[0] and "Reuters" in _pline[0])
check("the warning counts what was dropped", "1 collected but unused" in _pline[0])

print("\n=== 14m. A same-day re-run explains itself ===")
# Brief 3 failed on "OVERNIGHT ITEMS CRITICAL: only 2 (min 3)", which reads
# like a starved collector and was not: 65 items had already gone out that day
# and the cross-day memory stripped every one of them.
import io as _io_cap, contextlib as _ctx
_arch_gap.record_published({"also_today": [
    {"url": "http://example.com/sameday", "headline": "Something already sent",
     "category": "AU-Politics"}]})
_buf = _io_cap.StringIO()
with _ctx.redirect_stdout(_buf):
    run_mod._explain_same_day_rerun()
_out = _buf.getvalue()
check("it names how many items already went out", "already went out today" in _out)
check("it says the guard is working, not the collector failing",
      "guard working, not a" in _out)
check("it points at the first run of the day, not the caps",
      "FIRST run of the day" in _out)
check("it is wired into the give-up branch, not merely defined",
      "_explain_same_day_rerun()" in
      _rsrc_run.split("the brief will NOT be sent")[-1])

_wf = Path(".github/workflows/daily-brief.yml").read_text(encoding="utf-8")
check("the job budget has headroom over the observed worst case",
      "timeout-minutes: 45" in _wf)
check("the timeout says why it is not 30", "22 of its 30 minutes" in _wf)

print("\n=== 14n. Replacing today's issue is possible and narrow ===")
# The third run of 25 August died with 65 of its own items stripped out from
# under it. A replacement issue is not a repeat of the issue it replaces.
_before = len(_arch_gap.recent_published(days=3))
check("the archive has today's items to hide", _before > 0, f"{_before} items")
from datetime import datetime as _dt
from zoneinfo import ZoneInfo as _zi
_today_str = _dt.now(_zi("America/New_York")).strftime("%Y-%m-%d")
_arch_gap.exclude_date(_today_str)
check("excluding today hides them from the cross-day memory",
      len(_arch_gap.recent_published(days=3)) == 0)
check("and from the per-URL lookup the stale filter uses",
      _arch_gap.lookup_published("http://example.com/gap", "", days=7) is None)
_arch_gap.exclude_date(None)
check("clearing the exclusion restores the memory",
      len(_arch_gap.recent_published(days=3)) == _before)
check("the flag exists and says what it does",
      "--replace-today" in _rsrc_run and "hide today" in _rsrc_run)
check("it is never set on the scheduled path",
      "args.replace_today" in _rsrc_run
      and "_EXCLUDE_DATE" not in _rsrc_run)
check("the workflow exposes it as a dispatch input",
      "replace_today:" in _wf and "python run.py --replace-today" in _wf)
check("the workflow default is off", "default: false" in _wf)

print("\n=== 14o. Politico Canberra Playbook ===")
# Politico's Canberra desk product: the only daily on Australian federal
# politics any of the named international outlets runs.
check("the feed is registered",
      "Politico Canberra Playbook" in collect.TIER1_FEEDS)
check("it routes through Google News, since the RSS path is unverifiable",
      "news.google.com" in collect.TIER1_FEEDS["Politico Canberra Playbook"])
check("it uses the AU edition for an Australian product",
      "gl=AU" in collect.TIER1_FEEDS["Politico Canberra Playbook"])
check("it is distinct from the existing Politico region feed",
      collect.TIER1_FEEDS["Politico Canberra Playbook"]
      != collect.TIER1_FEEDS["Politico (region)"])
check("a qualifying item cannot be silently dropped",
      "Politico Canberra Playbook" in collect._PRESTIGE_FEEDS
      and "Politico Canberra Playbook" in run_mod._PRESTIGE_OUTLETS)
# One product reaches the pipeline under two source strings: the Google News
# feed reports the feed name, the IMAP path composes "<publisher> (<newsletter>)".
# The _NEWSLETTERS display name carries no publisher prefix, or the composed
# label doubles to "Politico (Politico Canberra Playbook)" and matches neither
# the prestige set nor anything a reader would want to see in a source line.
check("the subscriber issues have an IMAP fingerprint",
      any(k == "canberra playbook" for k, _ in newsletters._NEWSLETTERS))
_pb_label = newsletters._match_source(
    "Politico Australia <canberraplaybook@email.politico.com>",
    "Canberra Playbook: Marles under pressure on subs timetable")
check("the composed label does not double the publisher",
      _pb_label == "Politico (Canberra Playbook)", _pb_label)
check("both source strings are prestige",
      _pb_label in run_mod._PRESTIGE_OUTLETS
      and "Politico Canberra Playbook" in run_mod._PRESTIGE_OUTLETS)
check("it files as Australian, which is the default",
      collect._SOURCE_REGION.get("Politico Canberra Playbook", "AU") == "AU")

print("\n=== 14p. IMAP scan: headers first, All Mail fallback ===")
# Driven against a stub server, because the real one needs credentials and a
# subscription. What is being pinned is the fetch discipline and the fallback,
# both of which are invisible until a live morning goes wrong.
_RAW = (b"From: Politico Australia <canberraplaybook@email.politico.com>\r\n"
        b"Subject: Canberra Playbook: subs timetable slips\r\n"
        b"Content-Type: text/html; charset=utf-8\r\n\r\n"
        b'<html><body><a href="https://www.politico.com/news/2026/08/25/'
        b'marles-submarine-timetable-slips-00123456">Australia concedes the '
        b"AUKUS submarine timetable has slipped</a></body></html>\r\n")
_HDR = (b"From: Politico Australia <canberraplaybook@email.politico.com>\r\n"
        b"Subject: Canberra Playbook: subs timetable slips\r\n\r\n")
_NOISE_HDR = b"From: bank@example.com\r\nSubject: Your statement\r\n\r\n"

class _StubIMAP:
    """Minimal IMAP4_SSL stand-in. Records what was fetched, and from where."""
    def __init__(self, has_mail_in): self.has = has_mail_in; self.fetched = []
    def select(self, mailbox, readonly=False):
        self.box = mailbox
        return ("OK", None)
    def search(self, charset, query):
        return ("OK", [b"1 2"] if self.box == self.has else [b""])
    def fetch(self, mid, spec):
        self.fetched.append((mid, spec))
        if "HEADER.FIELDS" in spec:
            return ("OK", [(b"x", _HDR if mid == b"1" else _NOISE_HDR)])
        return ("OK", [(b"x", _RAW)])

_stub = _StubIMAP("INBOX")
_got = newsletters._scan_mailbox(_stub, "INBOX", 1)
check("a subscribed newsletter is found and parsed", len(_got) == 1, str(len(_got)))
check("the item carries the composed label",
      _got and _got[0].get("source") == "Politico (Canberra Playbook)")
_specs = [s for _, s in _stub.fetched]
check("every message is header-scanned first",
      sum("HEADER.FIELDS" in s for s in _specs) == 2)
check("only the match costs a body fetch",
      sum("HEADER.FIELDS" not in s for s in _specs) == 1)
check("PEEK is used, so the operator's mail stays unread",
      all("BODY.PEEK" in s for s in _specs))
check("a non-matching sender is skipped without a body fetch",
      (b"2", "(BODY.PEEK[])") not in _stub.fetched)

# The failure mode that matters: a Gmail filter labels the newsletter and skips
# the inbox, so an INBOX-only scan finds nothing while it arrives every morning.
_filed = _StubIMAP('"[Gmail]/All Mail"')
check("INBOX-only would have found nothing",
      newsletters._scan_mailbox(_filed, "INBOX", 1) == [])
check("All Mail finds a filtered newsletter",
      len(newsletters._scan_mailbox(_filed, '"[Gmail]/All Mail"', 1)) == 1)
_nsrc = inspect.getsource(newsletters)
check("from_imap tries All Mail after INBOX",
      '"INBOX", \'"[Gmail]/All Mail"\'' in _nsrc)
check("the scan window is not a hundred full messages any more",
      newsletters.MAX_MESSAGES >= 400)

print("\n=== 14q. The source list is published and current ===")
# The feed set only ever existed as four dicts in collect.py, so "what does
# this brief read?" meant opening the collector and counting. SOURCES.md is
# generated, and checked here, because a hand-maintained list is wrong the
# first time somebody adds a feed and nobody notices for a month.
import list_sources
_sources_path = Path("SOURCES.md")
check("SOURCES.md is committed", _sources_path.exists())
_on_disk = _sources_path.read_text(encoding="utf-8")
check("SOURCES.md matches the feed dicts (re-run list_sources.py if this fails)",
      _on_disk == list_sources.build())
_n_feeds = sum(len(f) for f in (collect.TIER1_FEEDS, collect.TIER2_FEEDS,
                                collect.TIER3_FEEDS, collect.TIER4_FEEDS))
check("every feed appears in it",
      all(name in _on_disk for name in collect.TIER1_FEEDS), f"{_n_feeds} feeds")
# Table rows only. The header carries a legend line with the same marker.
_marked = sum(1 for ln in _on_disk.splitlines()
              if ln.startswith("| ") and "**\\***" in ln)
check("every prestige feed is marked in the table",
      _marked == len(collect._PRESTIGE_FEEDS), f"{_marked} marked")
check("the marker is explained before it is used",
      _on_disk.index("marks a prestige feed") < _on_disk.index("| Source |"))
check("the newsletter fingerprints are listed",
      "canberra playbook" in _on_disk and "NEWSLETTERS" in _on_disk)
check("it says it is generated, not hand-edited",
      "Do not edit by hand" in _on_disk)

print("\n=== 14r. The journalist watch list actually fires ===")
# It never had. Across the 366 items collected on the first live runs it
# matched zero times, because it searched the title and the summary for a bare
# name and a byline appears in neither.
check("the beats are structured, not comments",
      len(collect.JOURNALIST_BEATS) == 4)
check("every name is reachable through the flat set",
      collect.PRESTIGE_JOURNALISTS ==
      {n for v in collect.JOURNALIST_BEATS.values() for n in v})
check("no name is listed under two beats",
      sum(len(v) for v in collect.JOURNALIST_BEATS.values())
      == len(collect.PRESTIGE_JOURNALISTS))

_j = lambda **kw: collect._flag_journalist(
    {"title": "x", "summary": "y", "author": "", **kw}).get("flagged_journalist")
check("the feed's author field flags",
      _j(author="Ben Packham") == "Ben Packham")
check("an author field with more than the name still flags",
      _j(author="By Kirsty Needham, Reuters") == "Kirsty Needham")
check("a By line at the head of the body flags",
      _j(summary="By Andrew Tillett. Canberra raised the budget.") == "Andrew Tillett")
check("a bare mention does NOT flag",
      _j(summary="David Speers pressed the minister on the timetable.") is None)
check("a By line deep in the body does not flag",
      _j(summary="x" * 400 + " by Laura Tingle") is None)
check("an unwatched byline is left alone",
      _j(author="Some Other Reporter") is None)

# The collector has to capture the byline at all, or none of the above can fire.
_csrc = inspect.getsource(collect)
check("_entry_to_article captures the author field",
      '"author": author' in _csrc and 'entry.get("author")' in _csrc)
check("re-flagging runs after full-text enrichment",
      "reflag_journalists" in _csrc
      and "reflag_journalists" in _rsrc_run
      and _rsrc_run.index("enrich_payload") < _rsrc_run.index("collect.reflag_journalists"))
_payload = {"tier1": [{"title": "t", "summary": "By Damien Cave. Something.",
                       "author": ""},
                      {"title": "t", "summary": "nothing here", "author": ""}]}
check("re-flagging finds a byline the feed did not publish",
      collect.reflag_journalists(_payload) == 1
      and _payload["tier1"][0]["flagged_journalist"] == "Damien Cave")
check("re-flagging does not re-count an already flagged item",
      collect.reflag_journalists(_payload) == 0)

check("the correspondents are published in SOURCES.md",
      "## Correspondents watched" in _on_disk
      and all(n in _on_disk for n in collect.PRESTIGE_JOURNALISTS))
check("SOURCES.md explains the byline test, not just the names",
      "mention, not a byline" in _on_disk)

print("\n=== 14s. Schedule slots and the double-send guard ===")
# GitHub's scheduler ran 9.6 hours late on this repo on 27 August, turning a
# morning brief into an evening one. Six slots instead of two so a single late
# slot no longer decides the day. That is only safe because the guard skips
# every slot after the first, so the cost of five extra slots is ~8s each and
# not five extra emails.
import yaml as _yaml
_wf_doc = _yaml.safe_load(_wf)
_crons = [c["cron"] for c in _wf_doc[True]["schedule"]]
check("six schedule slots", len(_crons) == 6, str(len(_crons)))
check("the first slot aims at the intended 6:00 AM ET hour", _crons[0] == "0 10 * * 1-5")
check("every slot is weekdays only", all(c.endswith("* * 1-5") for c in _crons))
check("slots are distinct", len(set(_crons)) == len(_crons))
check("slots are in ascending order",
      _crons == sorted(_crons, key=lambda c: (int(c.split()[1]), int(c.split()[0]))))
check("the comment records the measured delays, not a guess",
      "+9.6h" in _wf and "27 Aug" in _wf)
check("the daylight-saving caveat is stated", "daylight saving" in _wf)

# The guard is what makes six slots safe rather than six sends.
check("a dispatch never consults the guard", 'if [ "${{ github.event_name }}" != "schedule" ]' in _wf)
check("a slot skips once one has succeeded", 'elif [ "$success_count" -gt "0" ]' in _wf)
# A run counts ITSELF in the in-progress query, so the threshold is 1, not 0.
check("a slot skips while another is still in flight", '"$total" -gt "1"' in _wf)
check("runs queue rather than overlap",
      _wf_doc["concurrency"]["group"] == "daily-brief"
      and _wf_doc["concurrency"]["cancel-in-progress"] is False)

print("\n=== 15. Retry message shape ===")
# The retry paths must not end on an assistant turn (a prefill, rejected with a
# 400 on the current models) and must not ship the previous output twice.
_dsrc = inspect.getsource(digest_mod)
check("no assistant turn echoes the previous digest",
      '"role": "assistant"' not in _dsrc)
check("regenerate_digest documents that it holds FAST_MODEL first",
      "only the second escalates" in inspect.getdoc(digest_mod.regenerate_digest))

print("\n" + "=" * 50)
if FAILS:
    print(f"  {len(FAILS)} FAILURE(S): " + "; ".join(FAILS))
    sys.exit(1)
print("  ALL CHECKS PASSED")
