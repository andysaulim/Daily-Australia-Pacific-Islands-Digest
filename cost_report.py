"""
What the brief costs to run.

Modelled on the Korea brief's cost_report.py. The pipeline previously reported
token counts to the log and nowhere else, so a month of spend was invisible
unless someone read every run by hand. The first live issue burned a wasted
Sonnet attempt before Opus produced a clean brief, and nothing recorded that.

Reads metrics.jsonl, which run.py appends to after every send and the workflow
commits back, so this works from a fresh checkout.

    python cost_report.py            # last 30 days
    python cost_report.py --days 7
    python cost_report.py --json     # machine-readable, for a dashboard later

Prices are per million tokens and MUST be checked against
https://docs.anthropic.com/en/docs/about-claude/pricing before being trusted;
they are a local constant, not a live lookup, so they go stale silently. The
date below is when they were last confirmed.
"""
import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

METRICS = Path(__file__).parent / "metrics.jsonl"

# Confirmed 25 August 2026. Input / output, US dollars per million tokens.
PRICES_CHECKED = "2026-08-25"
PRICES = {
    "claude-opus-5":   (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-fable-5":  (10.00, 50.00),
    "claude-haiku-4-5": (1.00, 5.00),
}

# Cache writes cost ~1.25x input, cache reads ~0.1x. The pipeline caches the
# frozen system prompt, so reads dominate once a day's first call is made.
_CACHE_WRITE_MULT = 1.25
_CACHE_READ_MULT = 0.10


def cost_of(model: str, input_tokens: int = 0, output_tokens: int = 0,
            cache_write: int = 0, cache_read: int = 0) -> float:
    """Dollar cost of one call. Unknown model returns 0.0 rather than guessing."""
    if model not in PRICES:
        return 0.0
    in_rate, out_rate = PRICES[model]
    return ((input_tokens * in_rate)
            + (output_tokens * out_rate)
            + (cache_write * in_rate * _CACHE_WRITE_MULT)
            + (cache_read * in_rate * _CACHE_READ_MULT)) / 1_000_000


def load_metrics(days: int) -> list[dict]:
    cutoff = (datetime.now(ZoneInfo("America/New_York")).date()
              - timedelta(days=days)).strftime("%Y-%m-%d")
    try:
        lines = METRICS.read_text(encoding="utf-8").strip().splitlines()
    except FileNotFoundError:
        return []
    out = []
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("date", "") >= cutoff:
            out.append(row)
    return out


def report(days: int = 30) -> dict:
    rows = load_metrics(days)
    if not rows:
        return {"runs": 0, "days": days}

    sent = [r for r in rows if r.get("sent")]
    words = [r.get("word_count", 0) for r in rows if r.get("word_count")]
    retries = sum(r.get("validation_retries", 0) or 0 for r in rows)

    # Token counts are only present once run.py records them; older rows
    # predate that and are counted as runs but not as spend.
    priced = [r for r in rows if r.get("tokens")]
    total = 0.0
    by_model = {}
    for r in priced:
        for call in r["tokens"]:
            c = cost_of(call.get("model", ""), call.get("input", 0),
                        call.get("output", 0), call.get("cache_write", 0),
                        call.get("cache_read", 0))
            total += c
            by_model[call.get("model", "unknown")] = \
                by_model.get(call.get("model", "unknown"), 0.0) + c

    per_issue = total / len(priced) if priced else 0.0
    return {
        "days": days,
        "runs": len(rows),
        "sent": len(sent),
        "priced_runs": len(priced),
        "unpriced_runs": len(rows) - len(priced),
        "total_usd": round(total, 4),
        "per_issue_usd": round(per_issue, 4),
        "projected_monthly_usd": round(per_issue * 22, 2),   # ~22 weekdays
        "by_model_usd": {k: round(v, 4) for k, v in sorted(by_model.items())},
        "median_words": sorted(words)[len(words) // 2] if words else 0,
        "validation_retries": retries,
        "prices_checked": PRICES_CHECKED,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    rep = report(args.days)
    if args.json:
        print(json.dumps(rep, indent=2))
        return

    if not rep.get("runs"):
        print(f"\n  No runs recorded in the last {args.days} days.\n")
        return

    print(f"\n  Cost report, last {rep['days']} days")
    print(f"  {'-' * 46}")
    print(f"  Runs                 {rep['runs']} ({rep['sent']} sent)")
    print(f"  Median length        {rep['median_words']:,} words")
    print(f"  Validation retries   {rep['validation_retries']}")
    if rep["priced_runs"]:
        print(f"  Total               ${rep['total_usd']:.2f}")
        print(f"  Per issue           ${rep['per_issue_usd']:.2f}")
        print(f"  Projected monthly   ${rep['projected_monthly_usd']:.2f}  (22 weekdays)")
        for model, cost in rep["by_model_usd"].items():
            print(f"    {model:22} ${cost:.2f}")
    if rep["unpriced_runs"]:
        print(f"  {rep['unpriced_runs']} run(s) predate token recording and are not costed.")
    print(f"\n  Prices last confirmed {rep['prices_checked']}. Re-check them against")
    print("  the Anthropic pricing page before quoting these numbers.\n")


if __name__ == "__main__":
    # Self-test with synthetic figures matching the first live run.
    c = cost_of("claude-opus-5", input_tokens=64012, output_tokens=21775,
                cache_write=4651)
    assert 0.85 < c < 0.90, c          # 0.32 in + 0.544 out + 0.029 cache
    assert cost_of("not-a-model", 1000, 1000) == 0.0
    assert cost_of("claude-sonnet-5", 1_000_000, 0) == 2.00
    assert cost_of("claude-opus-5", 0, 1_000_000) == 25.00
    # A cache read is a tenth of an input token.
    assert abs(cost_of("claude-opus-5", cache_read=1_000_000) - 0.50) < 1e-9
    print(f"  one Opus run like the first live issue: ${c:.2f}")
    print("cost_report.py self-test passed\n")
    main()
