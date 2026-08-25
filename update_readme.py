"""
Australia Chair Daily Brief: README stats refresher
CSIS Australia Chair

Rewrites the block between the STATS markers in README.md with the last run's
numbers. Called by the workflow after the send, with continue-on-error, so a
failure here can never affect delivery.

The regional-balance line is the one worth watching: a run of days where the
Pacific section falls back to its stand-in means the feed set needs sources, not
a lower floor.
"""
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

README = Path(__file__).parent / "README.md"
METRICS = Path(__file__).parent / "metrics.jsonl"
START = "<!-- STATS:START -->"
END = "<!-- STATS:END -->"


def _load_metrics(limit: int = 30) -> list[dict]:
    if not METRICS.exists():
        return []
    rows = []
    for line in METRICS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows[-limit:]


def build_block() -> str:
    rows = _load_metrics()
    now = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %I:%M %p ET")

    if not rows:
        return f"{START}\n_No runs recorded yet. Updated {now}._\n{END}"

    last = rows[-1]
    recent = rows[-10:]
    avg_words = round(sum(r.get("word_count", 0) for r in recent) / len(recent))
    pac_standin = sum(1 for r in recent if r.get("pacific_stand_in"))
    nz_standin = sum(1 for r in recent if r.get("nz_stand_in"))
    avg_pacific = round(sum(r.get("pacific_wire", 0) for r in recent) / len(recent), 1)
    avg_nz = round(sum(r.get("new_zealand", 0) for r in recent) / len(recent), 1)
    retries = sum(r.get("validation_retries", 0) for r in recent)

    try:
        from archive import stats as archive_stats
        corpus = archive_stats()
    except Exception:
        corpus = {}

    lines = [
        START,
        f"**Last run:** {last.get('date', 'unknown')} &middot; "
        f"{last.get('word_count', 0):,} words &middot; "
        f"{'sent' if last.get('sent') else 'not sent'}",
        "",
        "| Metric | Last 10 issues |",
        "| --- | --- |",
        f"| Average length | {avg_words:,} words |",
        f"| Pacific Wire items per issue | {avg_pacific} |",
        f"| New Zealand items per issue | {avg_nz} |",
        f"| Issues where Pacific fell back to the stand-in | {pac_standin} of {len(recent)} |",
        f"| Issues where New Zealand fell back to the stand-in | {nz_standin} of {len(recent)} |",
        f"| Validation retries | {retries} |",
    ]
    if corpus:
        lines.append(f"| Articles in the archive | {corpus.get('items', 0):,} |")
        lines.append(f"| Issues published | {corpus.get('issues', 0)} |")

    if pac_standin >= 3:
        lines += ["", "> The Pacific section has fallen back to its stand-in three or more "
                      "times in the last ten issues. That is a feed problem, not a "
                      "threshold problem: add Pacific sources rather than lowering the floor."]

    lines += ["", f"_Updated {now}._", END]
    return "\n".join(lines)


def main() -> None:
    if not README.exists():
        print("README.md not found, nothing to update")
        return
    text = README.read_text(encoding="utf-8")
    if START not in text or END not in text:
        print("README.md has no STATS markers, nothing to update")
        return
    head, _, rest = text.partition(START)
    _, _, tail = rest.partition(END)
    README.write_text(head + build_block() + tail, encoding="utf-8")
    print("README stats updated")


if __name__ == "__main__":
    main()
