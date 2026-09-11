"""Reading files that live on the published site, not in the repo.

Every edition publishes to GitHub Pages from a directory that is gitignored,
and every CI runner starts clean. So an index that accumulates across runs —
the archive manifest, the corpus manifest, a monthly shard — does not exist
locally when a run begins.

Code that reads such a file, finds nothing, starts empty, appends today and
writes it back destroys the history. The Pages deploy keeps files it is not
publishing but overwrites the ones it is, so each run replaced a growing index
with a single-day stub. In the Korea edition this ran unnoticed for months:
the archive page listed one issue and the issue number never advanced, however
many briefs had been sent.

Two rules, both enforced here:

1. Read the published copy before appending to it.
2. Do not write at all when that read fails. Today's page is deployed either
   way and keep_files preserves the rest, so skipping an index costs one day
   of listing, while writing a stub costs every day before it.
3. Believe a 404 only from a site that is demonstrably serving. A missing
   index and an unpublished site answer identically, and the second is the
   case where the history exists and is about to be overwritten.

`trustworthy` in the return value is rule two. A caller that ignores it
reintroduces the defect.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


_SENTINEL = "latest.html"


def _site_is_serving(base: str, timeout: int) -> bool:
    """Is anything published at `base` at all?

    A 404 on an index means one of two very different things: the file has
    never been written, which is fine and means start empty; or the site is
    not serving, which is not fine, because the history is still there and we
    are about to replace it with a one-day stub. From the index alone the two
    are indistinguishable — both are 404.

    So ask for a file that every published run writes. If that 404s too, the
    site is not answering and no 404 from it means anything.

    The cost is one archive row at the inception of an edition, before any
    issue is published. The cost of the other reading, paid by the Australia
    edition while its Pages site was not serving, is every row.
    """
    try:
        import requests
        return requests.get(f"{base}/{_SENTINEL}", timeout=timeout).ok
    except Exception:                                           # noqa: BLE001
        return False


def load(path: Path, base_url: str = "", fallback=None, timeout: int = 15):
    """Return (data, trustworthy) for a file published under `base_url`.

    Local copy first, since a manual run may have one. Otherwise fetch from the
    published site. A 404 is trustworthy and empty only when the site answers
    for a file that must exist; otherwise every index on an unpublished site
    reads as legitimately absent. Any other failure is untrustworthy, and the
    caller must not write.
    """
    fallback = [] if fallback is None else fallback
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data:
            return data, True
    except (OSError, ValueError):
        pass

    base = (base_url or os.environ.get("WEB_URL", "")).rstrip("/")
    if not base:
        return fallback, False
    try:
        import requests
        resp = requests.get(f"{base}/{Path(path).name}", timeout=timeout)
        if resp.ok:
            data = resp.json()
            if isinstance(data, type(fallback)):
                return data, True
        if resp.status_code == 404:
            if _site_is_serving(base, timeout):
                return fallback, True
            print(f"    warning: {Path(path).name} 404s and so does "
                  f"{_SENTINEL} — {base} is not serving, so this is an "
                  f"unreadable index, not an empty one; it will not be "
                  f"rewritten this run")
            return fallback, False
    except Exception as exc:
        print(f"    warning: {Path(path).name} unreachable ({exc}); "
              f"it will not be rewritten this run")
    return fallback, False


def write_if_safe(path: Path, data, trustworthy: bool, *, indent=2) -> bool:
    """Write only when the history behind `data` was actually read."""
    if not trustworthy:
        print(f"    warning: {Path(path).name} NOT written — prior contents "
              f"could not be read, and overwriting would drop them")
        return False
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=indent),
                          encoding="utf-8")
    return True


def merge_seed(entries, seed_path: Path, key: str = "date"):
    """Restore rows the published index lost, from a copy kept in the repo.

    The published index is authoritative — it is the only thing that knows
    about issues sent since the last commit. But it has been lost before, and
    a run that reads a truncated index cannot tell truncation from a young
    edition: both are simply short.

    So an edition may keep a seed in the repo, rebuilt by hand from the issues
    still on the site. Rows the fetched index already has win outright; the
    seed only fills dates missing from it. That makes the seed a floor rather
    than a source of truth, and a stale seed cannot undo a later correction.
    """
    try:
        seed = json.loads(Path(seed_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return entries
    if not isinstance(seed, list):
        return entries
    have = {str(e.get(key)) for e in entries if isinstance(e, dict)}
    restored = [row for row in seed
                if isinstance(row, dict) and str(row.get(key)) not in have]
    if restored:
        print(f"    {len(restored)} archive row(s) restored from "
              f"{Path(seed_path).name}")
    return entries + restored
