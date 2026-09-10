"""
API cost from metrics.jsonl — by day, and rolled up by month.

    python cost_report.py              # last 30 days, then a monthly rollup
    python cost_report.py --days 90    # a longer daily window
    python cost_report.py --month 2026-09
    python cost_report.py --csv        # daily rows as CSV, for a spreadsheet

One line per run is appended by run.py. A run recorded before cost logging
existed has no est_cost_usd and is counted as "untracked" rather than as zero:
unknown spend and no spend are different things, and reporting the first as the
second is how a bill becomes a surprise.
"""
import argparse
import json
from collections import defaultdict
from pathlib import Path

FIELDS = ("api_calls", "input_tokens", "output_tokens",
          "cache_write_tokens", "cache_read_tokens", "est_cost_usd")

# The five editions grew their metrics separately and record cost three ways:
# a flat est_cost_usd with token totals (Korea, Japan, Middle East), a cost_usd
# alongside a per-call ledger (China), and a ledger with no cost field at all
# (Australia). Rather than rewrite three working pipelines, normalise on read —
# so one command answers "what did this cost" in any of them.
MODEL_PRICING = {
    "claude-opus-5":     {"input": 5.00, "output": 25.00, "cache_write": 6.25, "cache_read": 0.50},
    "claude-sonnet-5":   {"input": 2.00, "output": 10.00, "cache_write": 2.50, "cache_read": 0.20},
    "claude-opus-4-8":   {"input": 5.00, "output": 25.00, "cache_write": 6.25, "cache_read": 0.50},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00, "cache_write": 3.75, "cache_read": 0.30},
}
_DEFAULT_PRICE = {"input": 5.0, "output": 25.0, "cache_write": 6.25, "cache_read": 0.5}


def _price_call(call: dict) -> float:
    p = MODEL_PRICING.get(call.get("model", ""), _DEFAULT_PRICE)
    return (call.get("input", 0) * p["input"]
            + call.get("output", 0) * p["output"]
            + call.get("cache_write", 0) * p["cache_write"]
            + call.get("cache_read", 0) * p["cache_read"]) / 1_000_000


def normalise(row: dict) -> dict | None:
    """One run's usage in a single shape, or None if the run predates tracking."""
    ledger = row.get("tokens")
    if isinstance(ledger, list) and ledger:
        cost = row.get("est_cost_usd", row.get("cost_usd"))
        if not isinstance(cost, (int, float)):
            cost = sum(_price_call(c) for c in ledger if isinstance(c, dict))
        return {
            "api_calls": len(ledger),
            "input_tokens": sum(c.get("input", 0) for c in ledger if isinstance(c, dict)),
            "output_tokens": sum(c.get("output", 0) for c in ledger if isinstance(c, dict)),
            "cache_write_tokens": sum(c.get("cache_write", 0) for c in ledger if isinstance(c, dict)),
            "cache_read_tokens": sum(c.get("cache_read", 0) for c in ledger if isinstance(c, dict)),
            "est_cost_usd": float(cost),
        }
    cost = row.get("est_cost_usd", row.get("cost_usd"))
    if isinstance(cost, (int, float)):
        return {
            "api_calls": row.get("api_calls", 0),
            "input_tokens": row.get("input_tokens", 0),
            "output_tokens": row.get("output_tokens", 0),
            "cache_write_tokens": row.get("cache_write_tokens", 0),
            "cache_read_tokens": row.get("cache_read_tokens", 0),
            "est_cost_usd": float(cost),
        }
    return None


def load_metrics(path: str = "metrics.jsonl") -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue          # a half-written line from a killed run
    return rows


def _blank() -> dict:
    d = {f: 0 for f in FIELDS}
    d["est_cost_usd"] = 0.0
    d["runs"] = 0
    d["tracked_runs"] = 0
    d["sent"] = 0
    return d


def group_by(rows: list[dict], width: int) -> dict:
    """width=10 groups by day (YYYY-MM-DD), width=7 by month."""
    out = defaultdict(_blank)
    for row in rows:
        key = str(row.get("date", ""))[:width]
        if len(key) != width:
            continue
        g = out[key]
        g["runs"] += 1
        if row.get("sent"):
            g["sent"] += 1
        usage = normalise(row)
        if usage is not None:
            g["tracked_runs"] += 1
            for f in FIELDS:
                g[f] += usage[f]
    return dict(sorted(out.items()))


def _table(groups: dict, label: str, limit: int | None = None):
    if not groups:
        print(f"No {label} data in metrics.jsonl yet.")
        return
    items = list(groups.items())
    if limit:
        items = items[-limit:]
    head = (f"{label:<12}{'Runs':>5}{'Sent':>6}{'Calls':>7}"
            f"{'Tokens in':>13}{'Tokens out':>12}{'Cost':>10}{'$/run':>8}")
    print(head)
    print("-" * len(head))
    total = 0.0
    for key, g in items:
        cost = g["est_cost_usd"]
        total += cost
        per = cost / g["tracked_runs"] if g["tracked_runs"] else 0.0
        note = "" if g["tracked_runs"] == g["runs"] else f"  ({g['runs'] - g['tracked_runs']} untracked)"
        print(f"{key:<12}{g['runs']:>5}{g['sent']:>6}{g['api_calls']:>7}"
              f"{g['input_tokens']:>13,}{g['output_tokens']:>12,}"
              f"{cost:>9.2f}{per:>8.2f}{note}")
    print("-" * len(head))
    print(f"{'TOTAL':<12}{'':>5}{'':>6}{'':>7}{'':>13}{'':>12}{total:>9.2f}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=30, help="daily rows to show (default 30)")
    ap.add_argument("--month", help="show only this month, YYYY-MM")
    ap.add_argument("--csv", action="store_true", help="emit daily rows as CSV")
    ap.add_argument("--file", default="metrics.jsonl")
    args = ap.parse_args()

    rows = load_metrics(args.file)
    if not rows:
        print(f"No data in {args.file} yet. It is written once per run.")
        return 0

    daily = group_by(rows, 10)
    if args.month:
        daily = {k: v for k, v in daily.items() if k.startswith(args.month)}

    if args.csv:
        print("date,runs,sent,api_calls,input_tokens,output_tokens,est_cost_usd")
        for k, g in daily.items():
            print(f"{k},{g['runs']},{g['sent']},{g['api_calls']},"
                  f"{g['input_tokens']},{g['output_tokens']},{g['est_cost_usd']:.4f}")
        return 0

    _table(daily, "Day", limit=None if args.month else args.days)
    if not args.month:
        print()
        _table(group_by(rows, 7), "Month")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
