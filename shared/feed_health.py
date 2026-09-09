"""Track which feeds are actually delivering, run after run.

A feed does not fail loudly. It returns an empty list and the brief is quietly
a little thinner. Korea Herald, a designated major feed, returned zero for an
unknown number of weeks because its publisher moved the RSS path during a site
redesign; nothing in the pipeline noticed, because health was computed inside
`collect()` and read by nothing.

Per-run health cannot catch that either: on any given morning a feed may be
legitimately empty. What distinguishes a quiet feed from a dead one is the
streak. This module keeps that streak across runs in `feed_health.json`, which
is cached between GitHub Actions runs like the other trackers, and reports the
feeds that have gone silent long enough to be a real failure.

It also records which candidate URL answered. Nearly every feed here is a
Google News search rather than the outlet's own RSS, which makes one service a
single point of failure for the whole brief. Native feeds are being added in
front of the searches, and this is the measurement of that migration: the
report says how much of the brief still rests on Google News.

Nothing here raises. A tracker that breaks the pipeline is worse than no
tracker.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "feed_health.json")

# A feed silent this many consecutive runs is reported as a likely failure
# rather than a quiet day. Three weekdays is long enough that a real outlet
# would have published something.
SILENT_RUN_THRESHOLD = 3


def load() -> dict:
    try:
        with open(PATH, encoding="utf-8") as fh:
            data = json.load(fh)
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(state: dict) -> bool:
    try:
        with open(PATH, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=1, sort_keys=True)
        return True
    except OSError:
        return False


def record(per_source: dict, source_used: dict | None = None) -> dict:
    """Fold one run's results into the stored history and return the new state.

    `per_source` is collect()'s health map, {feed_name: {"success": bool, ...}}.
    `source_used` maps a URL to "native" or "google-news"; it is keyed by URL
    because a feed may have several candidates.
    """
    state = load()
    feeds = state.setdefault("feeds", {})
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    for name, health in (per_source or {}).items():
        rec = feeds.setdefault(name, {"silent_runs": 0, "last_ok": None, "runs": 0})
        rec["runs"] = int(rec.get("runs", 0)) + 1
        if health.get("success"):
            rec["silent_runs"] = 0
            rec["last_ok"] = today
        else:
            rec["silent_runs"] = int(rec.get("silent_runs", 0)) + 1

    if source_used:
        native = sum(1 for v in source_used.values() if v == "native")
        state["last_run_native"] = native
        state["last_run_google"] = sum(1 for v in source_used.values() if v == "google-news")

    state["last_run"] = today
    state["total_runs"] = int(state.get("total_runs", 0)) + 1
    return state


def report(state: dict, threshold: int = SILENT_RUN_THRESHOLD) -> list[str]:
    """Lines for the run log. Empty list means every feed is healthy."""
    feeds = (state or {}).get("feeds", {})
    lines: list[str] = []

    dead = sorted(((n, r) for n, r in feeds.items()
                   if int(r.get("silent_runs", 0)) >= threshold),
                  key=lambda kv: -int(kv[1].get("silent_runs", 0)))
    if dead:
        lines.append(f"{len(dead)} feed(s) silent for {threshold}+ consecutive runs:")
        for name, rec in dead[:20]:
            last = rec.get("last_ok") or "never"
            lines.append(f"    {name}: {rec['silent_runs']} runs, last delivered {last}")
        if len(dead) > 20:
            lines.append(f"    (+{len(dead) - 20} more)")

    native = state.get("last_run_native")
    google = state.get("last_run_google")
    if isinstance(native, int) and isinstance(google, int) and (native + google):
        pct = 100 * google / (native + google)
        lines.append(f"upstream mix: {native} native feeds, {google} via Google News "
                     f"({pct:.0f}% of delivering feeds depend on one service)")
    return lines


def update_and_report(per_source: dict, source_used: dict | None = None) -> list[str]:
    """Record this run and return the log lines. Never raises."""
    try:
        state = record(per_source, source_used)
        save(state)
        return report(state)
    except Exception as exc:                      # pragma: no cover - defensive
        return [f"feed health tracking failed: {exc}"]
