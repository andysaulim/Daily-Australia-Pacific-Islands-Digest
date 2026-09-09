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

`trustworthy` in the return value is rule two. A caller that ignores it
reintroduces the defect.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def load(path: Path, base_url: str = "", fallback=None, timeout: int = 15):
    """Return (data, trustworthy) for a file published under `base_url`.

    Local copy first, since a manual run may have one. Otherwise fetch from the
    published site. A 404 is treated as trustworthy and empty: that is a file
    which legitimately does not exist yet, such as the first shard of a new
    month. Any other failure is untrustworthy, and the caller must not write.
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
            return fallback, True
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
