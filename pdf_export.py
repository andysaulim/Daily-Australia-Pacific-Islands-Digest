"""
Australia Chair Daily Brief: PDF export
CSIS Australia Chair

Renders an issue's HTML to a paginated PDF with headless Chromium, so the
archive offers a real file rather than a page and a print dialogue. A PDF is
what gets attached to an email, filed in a folder, or handed to someone who
does not want a GitHub Pages link.

Best-effort by design. Every entry point returns False rather than raising, and
run.py generates the PDF after the issue is already written and before the send
without letting a failure touch either. The brief arriving matters; the PDF is
a convenience.

Chromium rather than a pure-Python renderer because the issue is email HTML:
nested presentational tables, inline styles, a fixed 680px frame. WeasyPrint
and friends handle that badly, and a PDF that misrenders the brief is worse
than no PDF.
"""
import os
from pathlib import Path

# The issue frame is 680px wide. 8.5in at 96dpi is 816px, which leaves a
# sensible margin either side without scaling the type down.
PAGE_FORMAT = "Letter"
MARGIN = {"top": "0.5in", "bottom": "0.5in", "left": "0.5in", "right": "0.5in"}

# Chromium has to finish web fonts and layout, not network requests: the HTML
# is self-contained. "load" is enough and does not wait on anything external
# that may never answer.
WAIT_UNTIL = "load"
TIMEOUT_MS = 30_000


def available() -> bool:
    """Is Playwright importable and a browser present?"""
    try:
        from playwright.sync_api import sync_playwright       # noqa: F401
    except ImportError:
        return False
    return True


def to_pdf(html_path: str | Path, pdf_path: str | Path) -> bool:
    """Render one HTML file to PDF. Returns True only on a written file.

    Loads through a file:// URL rather than set_content so that relative
    references and the document's own base resolve the way they would in a
    browser.
    """
    html_path, pdf_path = Path(html_path), Path(pdf_path)
    if not html_path.is_file():
        print(f"  !  [pdf] no such file: {html_path}")
        return False
    if not available():
        print("  !  [pdf] playwright not installed, skipping PDF")
        return False

    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as p:
            # CHROMIUM_PATH points at an already-installed binary, for hosts
            # that ship Chromium but not the exact build this Playwright
            # expects. Without it the driver insists on its own pinned build
            # and fails on a machine that already has a perfectly good browser.
            launch = {"args": ["--no-sandbox"]}
            exe = os.environ.get("CHROMIUM_PATH", "").strip()
            if exe:
                launch["executable_path"] = exe
            browser = p.chromium.launch(**launch)
            try:
                page = browser.new_page()
                page.goto(html_path.resolve().as_uri(),
                          wait_until=WAIT_UNTIL, timeout=TIMEOUT_MS)
                pdf_path.parent.mkdir(parents=True, exist_ok=True)
                page.pdf(
                    path=str(pdf_path),
                    format=PAGE_FORMAT,
                    margin=MARGIN,
                    # Without this the navy header and the section rules come
                    # out white: Chromium drops background graphics in print by
                    # default, and this brief is mostly background colour.
                    print_background=True,
                    prefer_css_page_size=False,
                )
            finally:
                browser.close()
    except Exception as e:                                     # noqa: BLE001
        print(f"  !  [pdf] render failed, continuing without it: {e}")
        return False

    if not pdf_path.is_file() or pdf_path.stat().st_size < 1000:
        print(f"  !  [pdf] output missing or implausibly small: {pdf_path}")
        return False

    print(f"  [pdf] {pdf_path.name} ({pdf_path.stat().st_size:,} bytes)")
    return True


if __name__ == "__main__":
    import sys
    src = sys.argv[1] if len(sys.argv) > 1 else "latest.html"
    dst = sys.argv[2] if len(sys.argv) > 2 else "latest.pdf"
    ok = to_pdf(src, dst)
    print("PDF written" if ok else "PDF not written")
    sys.exit(0 if ok else 1)
