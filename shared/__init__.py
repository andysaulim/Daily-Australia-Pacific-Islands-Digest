"""Shared engine for the CSIS daily briefs.

Every module in this package is byte-identical across the Korea, China, Japan
and Australia repositories. Nothing region-specific belongs here — that lives
in each repo's root `brand.py` and its own feed lists.

The four briefs were built by cloning one another, so the same logic now exists
four times and has drifted: four wordings of the same editorial rules, four
subject-line formats, four masthead treatments, and a chair name copied from
Korea into China's product. This package is where that stops.

To adopt in another edition:
  1. Copy this directory in unchanged. Do not edit it per repo — per-repo edits
     are what caused the drift.
  2. Provide a root `brand.py` defining BRAND (see the Korea repo for shape).
  3. Run `python -m shared` and confirm the hash matches the other repos.

A change to shared behaviour is made here once and copied to all four.
"""
from __future__ import annotations

__version__ = "1.1.0"

__all__ = ["__version__", "fingerprint"]


def fingerprint() -> str:
    """A hash over every module in the package.

    Printed by `python -m shared`. Identical output in all four repositories
    means they are genuinely running the same engine; a difference means
    somebody edited a copy and the drift has already started.
    """
    import hashlib
    from pathlib import Path
    here = Path(__file__).parent
    digest = hashlib.sha256()
    for path in sorted(here.glob("*.py")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]
