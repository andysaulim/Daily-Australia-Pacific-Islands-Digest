"""
Pipeline health monitor.

Ported from the Korea brief's `pipeline_health.py`, which was written after a
full production outage: Anthropic retired the model IDs the pipeline pinned,
and nothing in the code noticed until the send failed. The lesson generalises.
Most of what breaks a brief like this breaks quietly, and the failure shows up
as a thinner brief rather than a red run.

Runs after the digest, prints findings inline, and returns a structured report
that run.py folds into metrics.jsonl so a slow drift is visible as a trend
rather than as one bad morning.

What it watches, and why each one earned its place:

  - Baseline staleness. `_REGIONAL_BASELINES` states ministries and rates as
    fact. The Korea analogue is the Gallup baseline; here the block carries a
    "Verified as at" date and this checks how far behind it has fallen.
  - Model deprecation. The exact failure that motivated the Korea original.
  - Tier coverage. A tier collapsing to near zero means feeds died, and the
    brief gets quietly thinner rather than failing.
  - Prestige outlet gap. The requester named eleven outlets and the prompt
    carries a mandatory-inclusion rule for them. If none appear in the input,
    that rule cannot fire and nobody would know.
  - Pacific stand-in frequency. The README's own pilot check: three or more
    stand-ins in ten issues means the Pacific feed set needs sources, not a
    lower floor. Reads the trailing window from metrics.jsonl.

Stdlib only. No network.
"""
import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

METRICS = Path(__file__).parent / "metrics.jsonl"

# The baselines block is hand-maintained, so it drifts on a human timescale.
# A ministry can survive months; a cash rate cannot. Thirty days is roughly
# "two RBA decisions have happened since anyone looked".
BASELINE_STALE_DAYS = 30
BASELINE_ALERT_DAYS = 60

# Floors, not targets. Tier 1 runs ~80 feeds, so 40 means roughly half died.
#
# tier3 is 0 on purpose. Academic journals publish on a monthly cycle, so a
# daily floor fires on a perfectly normal day, and a warning that cries wolf
# every morning is one nobody reads. A journal drought only means something
# sustained, which the trailing check below looks for instead.
TIER_EXPECTED_MIN = {"tier1": 40, "tier2": 6, "tier3": 0, "tier4": 2}

# Consecutive runs of an empty tier 3 before it is worth saying anything.
TIER3_DRY_RUNS = 6

# The eleven the Australia Chair asked for, as they appear in collect.py's feed
# names. The mandatory-inclusion rule in the prompt is written against these.
PRESTIGE_SOURCES = {
    "The Australian", "SMH", "SMH Federal Politics", "SMH World", "AFR",
    "ABC News", "ABC Politics", "ABC World", "ABC Pacific",
    "WSJ", "NYT Asia Pacific", "NYT (region)",
    "Politico Defense", "Politico (region)",
    "RNZ Pacific", "RNZ Pacific (wire)", "Islands Business",
    "Islands Business (wire)", "Pacific Island Times",
    "Australian Foreign Affairs",
}

# Kept in sync with digest.py by hand. An ID that drops off this set is the
# signal to check the model list before the API starts returning 404s.
KNOWN_MODEL_IDS = {
    "claude-opus-5", "claude-sonnet-5", "claude-fable-5",
    "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6",
    "claude-sonnet-4-6", "claude-haiku-4-5",
}

PACIFIC_STAND_IN_WINDOW = 10
PACIFIC_STAND_IN_LIMIT = 3


def _today():
    return datetime.now(ZoneInfo("America/New_York"))


def _baseline_verified_date(block: str):
    """Pull the 'Verified as at 25 August 2026' stamp off the baselines block."""
    m = re.search(r"Verified as at\s+(\d{1,2}\s+[A-Z][a-z]+\s+20\d{2})", block)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%d %B %Y").replace(
            tzinfo=ZoneInfo("America/New_York"))
    except ValueError:
        return None


def _recent_metrics(limit: int) -> list[dict]:
    try:
        lines = METRICS.read_text(encoding="utf-8").strip().splitlines()
    except FileNotFoundError:
        return []
    out = []
    for line in lines[-limit:]:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def check(payload: dict | None = None, digest: dict | None = None) -> dict:
    """Run every check. Returns {"warnings": [...], "alerts": [...], ...}."""
    warnings: list[str] = []
    alerts: list[str] = []

    # ── Baseline staleness ───────────────────────────────────────────────
    try:
        from digest import _REGIONAL_BASELINES
        verified = _baseline_verified_date(_REGIONAL_BASELINES)
        if verified is None:
            warnings.append(
                "Baselines carry no 'Verified as at' date, so staleness cannot "
                "be tracked. Add one when you next edit the block.")
            age = None
        else:
            age = (_today() - verified).days
            if age >= BASELINE_ALERT_DAYS:
                alerts.append(
                    f"Regional baselines last verified {age} days ago. Ministries "
                    f"and cash rates in the prompt are being stated as fact on "
                    f"{age}-day-old checking.")
            elif age >= BASELINE_STALE_DAYS:
                warnings.append(
                    f"Regional baselines last verified {age} days ago. Worth a pass.")
    except Exception as e:                                  # noqa: BLE001
        warnings.append(f"Could not read the baselines block: {e}")
        age = None

    # ── Model deprecation ────────────────────────────────────────────────
    try:
        from digest import FAST_MODEL, PRIMARY_MODEL
        for label, mid in (("FAST_MODEL", FAST_MODEL), ("PRIMARY_MODEL", PRIMARY_MODEL)):
            if mid not in KNOWN_MODEL_IDS:
                alerts.append(
                    f"{label} is '{mid}', which is not in this file's known-current "
                    f"set. Either it was retired, or KNOWN_MODEL_IDS needs updating "
                    f"after a deliberate upgrade. A retired ID takes the brief down.")
    except Exception as e:                                  # noqa: BLE001
        warnings.append(f"Could not read the model IDs: {e}")

    # ── Tier coverage ────────────────────────────────────────────────────
    tier_counts = {}
    if payload:
        for tier, minimum in TIER_EXPECTED_MIN.items():
            n = len(payload.get(tier) or [])
            tier_counts[tier] = n
            if n < minimum:
                warnings.append(
                    f"{tier} collected {n} items, below the {minimum} floor. "
                    f"Check the source-health line for dead feeds.")

    # ── Prestige outlet gap ──────────────────────────────────────────────
    if payload:
        seen = {a.get("source", "") for a in (payload.get("tier1") or [])}
        hits = seen & PRESTIGE_SOURCES
        if not hits:
            alerts.append(
                "No named prestige outlet appeared in tier 1 at all. The "
                "mandatory-inclusion rule cannot fire, and the brief will read "
                "as though those outlets published nothing.")
        elif len(hits) < 3:
            warnings.append(
                f"Only {len(hits)} named prestige outlet(s) in tier 1: "
                f"{', '.join(sorted(hits))}.")

    # ── Sustained journal drought ────────────────────────────────────────
    # A single empty day is normal; a fortnight of them means the tier 3
    # queries have stopped matching anything and need rewriting.
    recent_t3 = _recent_metrics(TIER3_DRY_RUNS)
    t3_counts = [m.get("tier_counts", {}).get("tier3") for m in recent_t3]
    if len(recent_t3) >= TIER3_DRY_RUNS and all(c == 0 for c in t3_counts if c is not None) \
            and any(c is not None for c in t3_counts):
        warnings.append(
            f"tier3 has collected nothing for {len(recent_t3)} consecutive runs. "
            f"The journal queries are matching nothing and need rewriting.")

    # ── Pacific stand-in frequency ───────────────────────────────────────
    recent = _recent_metrics(PACIFIC_STAND_IN_WINDOW)
    stand_ins = sum(1 for m in recent if m.get("pacific_stand_in"))
    if len(recent) >= 5 and stand_ins >= PACIFIC_STAND_IN_LIMIT:
        warnings.append(
            f"Pacific stand-in used in {stand_ins} of the last {len(recent)} "
            f"issues. Per the README that means the Pacific feed set needs "
            f"sources, not a lower floor.")

    report = {
        "baseline_age_days": age,
        "tier_counts": tier_counts,
        "pacific_stand_ins_recent": stand_ins,
        "recent_issues_considered": len(recent),
        "warnings": warnings,
        "alerts": alerts,
    }

    print("\n  Pipeline health:")
    if not warnings and not alerts:
        print("    All checks clear.")
    for a in alerts:
        print(f"    ALERT: {a}")
    for w in warnings:
        print(f"    warn:  {w}")

    return report


if __name__ == "__main__":
    import sys
    payload = None
    p = Path("collected.json")
    if p.exists():
        payload = json.loads(p.read_text(encoding="utf-8"))
    rep = check(payload)
    sys.exit(1 if rep["alerts"] else 0)
