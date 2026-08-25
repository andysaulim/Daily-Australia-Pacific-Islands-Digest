"""
Update README.md with live stats after each digest run.
Reads digest.json and collected.json to extract current metrics.
"""
import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


def update_readme():
    readme_path = Path("README.md")
    if not readme_path.exists():
        return

    readme = readme_path.read_text()

    # Gather stats from latest run artifacts
    stats = {}
    et = ZoneInfo("America/New_York")
    stats["last_run"] = datetime.now(et).strftime("%B %-d, %Y at %-I:%M %p ET")

    # From collected.json
    collected_path = Path("collected.json")
    if collected_path.exists():
        try:
            collected = json.loads(collected_path.read_text())
            stats["tier1_count"] = len(collected.get("tier1", []))
            stats["tier2_count"] = len(collected.get("tier2", []))
            stats["tier3_count"] = len(collected.get("tier3", []))
            stats["tier4_count"] = len(collected.get("tier4", []))
            stats["total_collected"] = (
                stats["tier1_count"] + stats["tier2_count"]
                + stats["tier3_count"] + stats["tier4_count"]
            )
            # Count unique sources
            sources = set()
            for tier_key in ("tier1", "tier2", "tier3", "tier4"):
                for article in collected.get(tier_key, []):
                    src = article.get("source", "")
                    if src:
                        sources.add(src)
            stats["unique_sources"] = len(sources)
        except (json.JSONDecodeError, KeyError):
            pass

    # From digest.json
    digest_path = Path("digest.json")
    if digest_path.exists():
        try:
            digest = json.loads(digest_path.read_text())
            stats["top_stories"] = len(digest.get("top_stories", []))
            stats["overnight_items"] = len(digest.get("overnight_items", []))
            stats["digest_date"] = digest.get("digest_date", "")

            # Word count estimate
            words = 0
            for section in ("top_stories", "overnight_items", "also_today",
                            "business_economy", "northeast_asia"):
                for item in (digest.get(section) or []):
                    for field in ("body", "body_text", "headline", "so_what",
                                  "pattern_note", "detail"):
                        words += len(str(item.get(field, "")).split())
            stats["word_count"] = words

            # Kim tracker
            kim = digest.get("kcna_delta", {}) or {}
            stats["kim_appeared"] = "Yes" if kim.get("kim_appearance_today") else "No"
        except (json.JSONDecodeError, KeyError):
            pass

    # Build stats block
    stats_lines = [
        "<!-- STATS:START -->",
        "## Latest Run",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
    ]

    if "last_run" in stats:
        stats_lines.append(f"| Last generated | {stats['last_run']} |")
    if "digest_date" in stats:
        stats_lines.append(f"| Digest date | {stats['digest_date']} |")
    if "total_collected" in stats:
        stats_lines.append(f"| Articles collected | {stats['total_collected']} |")
    if "unique_sources" in stats:
        stats_lines.append(f"| Unique sources | {stats['unique_sources']} |")
    if "top_stories" in stats:
        stats_lines.append(f"| Top stories | {stats['top_stories']} |")
    if "overnight_items" in stats:
        stats_lines.append(f"| Overnight items | {stats['overnight_items']} |")
    if "word_count" in stats:
        stats_lines.append(f"| Word count | ~{stats['word_count']:,} |")
    if "kim_appeared" in stats:
        stats_lines.append(f"| Kim Jong Un appeared | {stats['kim_appeared']} |")

    stats_lines.append("")
    stats_lines.append("<!-- STATS:END -->")

    stats_block = "\n".join(stats_lines)

    # Replace existing stats block or insert after first ---
    if "<!-- STATS:START -->" in readme:
        readme = re.sub(
            r"<!-- STATS:START -->.*?<!-- STATS:END -->",
            stats_block,
            readme,
            flags=re.DOTALL,
        )
    else:
        # Insert after the first --- separator
        first_hr = readme.find("\n---\n")
        if first_hr != -1:
            insert_pos = first_hr + len("\n---\n")
            readme = readme[:insert_pos] + "\n" + stats_block + "\n" + readme[insert_pos:]

    readme_path.write_text(readme)
    print(f"  README.md updated with stats from {stats.get('digest_date', 'latest run')}")


if __name__ == "__main__":
    update_readme()
