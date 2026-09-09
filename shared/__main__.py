"""`python -m shared` — report the engine version and drift fingerprint."""
from . import __version__, fingerprint

print(f"csis shared engine {__version__}  sha256:{fingerprint()}")
print("Run this in every edition; the line must match. A different hash means")
print("a copy was edited locally and the four briefs have started to drift.")
