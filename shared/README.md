# The shared engine

Byte-identical in the Korea, China, Japan and Australia repositories.

The four briefs were built by cloning one another, so the same logic exists
four times and has drifted. Korea gained guards after incidents that never
reached the other three. China shipped for months with Korea's chair name in
its masthead and footer. Each brief validates its output differently, or in two
cases barely at all.

This directory is where that stops. A change to shared behaviour is made here
once and copied to all four.

## What is in here

| Module | Holds | Why it is shared |
|---|---|---|
| `editorial.py` | The rules the model writes under | Four wordings of the same intent had already diverged; a rule earned from one brief's incident never reached the others |
| `validate.py` | Deterministic pre-send checks | These lived only in Korea's `run.py`, so three briefs send without them |
| `masthead.py` | Banner, link bar, disclaimer | Four hand-written mastheads, four sets of measurements |

## What is not in here, and why

**Anything region-specific.** Feed lists, trackers, market tickers, section
line-ups and the editorial focus belong to each edition. They are the reason
the four briefs exist separately.

**Per-repo branding.** Each repository keeps a root `brand.py` holding the
dozen values that legitimately differ: chair name, title, accent colour, flag
rule, contact. `masthead.py` reads it; it never contains it.

## Adopting it in another edition

1. Copy this directory in unchanged. **Do not edit it per repo** — per-repo
   edits are precisely what caused the drift.
2. Add a root `brand.py`. Copy Korea's and change the values.
3. Wire the pieces one at a time, verifying each before the next:
   - `validate.py` first. It is additive: call `run_all()` alongside whatever
     checks already exist and append its findings as warnings. Nothing is
     removed, so nothing can regress.
   - `masthead.py` next. Visual only.
   - `editorial.py` last, and deliberately. Swapping a live, tuned prompt is
     the one step here that can degrade a brief, so diff the assembled prompt
     against the existing one before running it.
4. Run `python -m shared` and confirm the hash matches the other repos.

## Detecting drift

```bash
python -m shared
# csis shared engine 1.0.0  sha256:779fa7f2ef6f57db
```

Run it in all four. The line must be identical. A different hash means a copy
was edited locally and the briefs have started to diverge again — restore the
canonical directory and move the change into `brand.py`, or make it here and
copy it to all four.

## Current adoption

| Edition | `validate.py` | `masthead.py` | `editorial.py` |
|---|---|---|---|
| Korea | wired, additive | present, not yet wired into `render.py` | present, not yet wired |
| China | not adopted | not adopted | not adopted |
| Japan | not adopted | not adopted | not adopted |
| Australia | not adopted | not adopted | not adopted |

Korea is the reference edition. The value of this package is realised when the
other three adopt it; until then it mostly documents what they are missing.
