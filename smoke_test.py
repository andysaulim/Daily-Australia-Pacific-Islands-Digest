"""Offline smoke test for the Australia Chair Daily Brief pipeline.

Exercises everything except the network and the Claude call: filters, region
tagging, trackers, the archive's cross-day memory, the validator, dedup, source
caps, URL repair, and the renderer.

    python smoke_test.py

Writes smoke_output.html so a layout change can be eyeballed without spending an
API call. Run this before every commit that touches the validator or renderer.
"""
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
check("signal badges rendered", "DEVELOPMENT" in html or "ESCALATION" in html)
check("category accent colour applied", render_mod._cat_color("China-Pacific") == "#8B0000")
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

print("\n" + "=" * 50)
if FAILS:
    print(f"  {len(FAILS)} FAILURE(S): " + "; ".join(FAILS))
    sys.exit(1)
print("  ALL CHECKS PASSED")
