"""
Australia Chair Daily Brief: AUKUS milestone tracker
CSIS Australia Chair

A persistent ledger of where AUKUS actually stands, injected into the digest
prompt so the model never has to answer "what is the current state of Pillar 1"
from memory. Same contract as the Korea brief's trackers:

    build_context_block()      -> str    read, for prompt injection
    update_from_digest(digest) -> int    write, only after validation passes

The seeded milestones below are STARTING STATE and carry a `confidence` field.
Anything marked "seed" has not been verified against two independent sources
yet. Verify and re-seed before the first supervised send: a wrong AUKUS date in
front of this readership is the one error that costs the product its credibility.
"""
import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

TRACKER_PATH = Path(__file__).parent / "data" / "aukus_tracker.json"

# Headline matcher for items worth writing back to the ledger.
_AUKUS_PATTERN = re.compile(
    r"aukus|virginia-class|virginia class|nuclear-powered submarine"
    r"|submarine rotational force|\bsrf-west\b|hmas stirling|osborne"
    r"|henderson (?:shipyard|precinct)|\bssn-aukus\b|astute-class"
    r"|naval nuclear propulsion|pillar (?:one|two|1|2)",
    re.IGNORECASE,
)

# Pillar 2 workstreams, as named in the trilateral statements.
PILLAR2_WORKSTREAMS = [
    "undersea capabilities",
    "quantum technologies",
    "artificial intelligence and autonomy",
    "advanced cyber",
    "hypersonic and counter-hypersonic",
    "electronic warfare",
    "innovation",
    "information sharing",
]

_SEED = {
    "last_updated": None,
    "pillar1": [
        {
            "milestone": "Submarine Rotational Force West at HMAS Stirling",
            "status": "seed, unverified",
            "note": "US and UK submarine rotations through Western Australia. "
                    "Confirm current rotation cadence and personnel numbers.",
            "confidence": "seed",
            "source_url": None,
            "last_seen": None,
        },
        {
            "milestone": "Virginia-class sale to Australia",
            "status": "seed, unverified",
            "note": "Confirm the number of boats, the delivery window, and the "
                    "status of US congressional authorisation.",
            "confidence": "seed",
            "source_url": None,
            "last_seen": None,
        },
        {
            "milestone": "Australian payments to the US submarine industrial base",
            "status": "seed, unverified",
            "note": "Confirm total committed, amount transferred to date, and "
                    "the schedule of remaining instalments.",
            "confidence": "seed",
            "source_url": None,
            "last_seen": None,
        },
        {
            "milestone": "Osborne shipyard, South Australia (SSN-AUKUS build)",
            "status": "seed, unverified",
            "note": "Construction and workforce milestones for the build yard.",
            "confidence": "seed",
            "source_url": None,
            "last_seen": None,
        },
        {
            "milestone": "Henderson precinct, Western Australia (sustainment)",
            "status": "seed, unverified",
            "note": "Dry dock and sustainment infrastructure decisions.",
            "confidence": "seed",
            "source_url": None,
            "last_seen": None,
        },
        {
            "milestone": "ITAR and export-control reform",
            "status": "seed, unverified",
            "note": "Licence-free defence trade between the three partners; "
                    "confirm current exemption scope and excluded technologies.",
            "confidence": "seed",
            "source_url": None,
            "last_seen": None,
        },
    ],
    "pillar2": [
        {"workstream": w, "status": "no recent reporting", "note": None,
         "source_url": None, "last_seen": None}
        for w in PILLAR2_WORKSTREAMS
    ],
    "recent_events": [],
}


def _today() -> str:
    return datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")


def _load() -> dict:
    try:
        return json.loads(TRACKER_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return json.loads(json.dumps(_SEED))


def _save(data: dict) -> None:
    TRACKER_PATH.parent.mkdir(parents=True, exist_ok=True)
    TRACKER_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                            encoding="utf-8")


def build_context_block() -> str:
    """Render the ledger for prompt injection."""
    data = _load()
    lines = ["AUKUS MILESTONE TRACKER (persistent state, use these, do not recall from memory)"]

    updated = data.get("last_updated")
    lines.append(f"  Tracker last updated: {updated or 'never'}")
    lines.append("  PILLAR 1, nuclear-powered submarines:")
    for m in data.get("pillar1", []):
        conf = m.get("confidence")
        flag = "  [UNVERIFIED SEED: do not assert as fact]" if conf == "seed" else ""
        seen = f" (last reported {m['last_seen']})" if m.get("last_seen") else ""
        lines.append(f"    - {m['milestone']}: {m['status']}{seen}{flag}")
        if m.get("note"):
            lines.append(f"        {m['note']}")

    active_p2 = [w for w in data.get("pillar2", []) if w.get("last_seen")]
    lines.append("  PILLAR 2, advanced capabilities:")
    if active_p2:
        for w in active_p2:
            lines.append(f"    - {w['workstream']}: {w['status']} "
                         f"(last reported {w['last_seen']})")
            if w.get("note"):
                lines.append(f"        {w['note']}")
    else:
        lines.append("    - No Pillar 2 workstream has been reported on yet in this brief.")

    events = data.get("recent_events", [])[:10]
    if events:
        lines.append("  RECENT AUKUS ITEMS CARRIED IN THIS BRIEF:")
        for e in events:
            lines.append(f"    - {e['date']}: {e['headline']}")

    lines.append("  RULE: report a milestone's status ONLY from this tracker or from "
                 "today's articles. Do NOT supply AUKUS dates, boat counts, or dollar "
                 "figures from memory. A milestone marked UNVERIFIED SEED must not be "
                 "stated as fact, omit it instead.")
    return "\n".join(lines)


def update_from_digest(digest: dict) -> int:
    """Write today's AUKUS items back to the ledger. Returns items recorded."""
    data = _load()
    today = _today()
    recorded = 0

    sections = ("top_stories", "aukus_watch", "overnight_items",
                "primary_documents", "also_today")
    for section in sections:
        for item in (digest.get(section) or []):
            if not isinstance(item, dict):
                continue
            headline = item.get("headline", "") or ""
            body = " ".join(str(item.get(f, "")) for f in
                            ("body", "body_text", "summary", "detail"))
            if not _AUKUS_PATTERN.search(f"{headline} {body}"):
                continue

            data.setdefault("recent_events", []).insert(0, {
                "date": today,
                "headline": headline,
                "url": item.get("url"),
                "section": section,
            })
            recorded += 1

            # Touch any Pillar 2 workstream this item names.
            for w in data.get("pillar2", []):
                if w["workstream"].split()[0].lower() in f"{headline} {body}".lower():
                    w["status"] = "reported"
                    w["note"] = headline[:160]
                    w["source_url"] = item.get("url")
                    w["last_seen"] = today

    # Keep the event log bounded: 60 entries is roughly a quarter.
    data["recent_events"] = data.get("recent_events", [])[:60]
    if recorded:
        data["last_updated"] = today
        _save(data)
    return recorded


if __name__ == "__main__":
    # Materialise the seed ledger so it can be edited by hand. SETUP step 2
    # sends the operator here to generate the file before the first send.
    if not TRACKER_PATH.exists():
        _save(_load())
        print(f"Wrote seed ledger to {TRACKER_PATH}")
        print("Every milestone marked \"confidence\": \"seed\" is unverified. "
              "Change it to \"confirmed\" only for lines sourced twice.\n")
    print(build_context_block())
