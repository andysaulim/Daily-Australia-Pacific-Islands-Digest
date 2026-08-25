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
