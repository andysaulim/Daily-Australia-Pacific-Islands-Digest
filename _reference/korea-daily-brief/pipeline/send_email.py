"""
Korea Daily Brief — Email Sender
Sends the rendered HTML digest via Gmail SMTP (app password).
"""
import os
import re
import smtplib
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# ─────────────────────────────────────────────────────────────────────────────
# HTML → Plain Text Converter
# ─────────────────────────────────────────────────────────────────────────────

def _html_to_plain_text(html: str) -> str:
    """Convert digest HTML into readable plain text for text-only email clients.

    Uses regex-based transformations (no external HTML parser required).
    The output is readable in a terminal or plain-text email viewer.
    """
    text = html

    # Remove <head>...</head> entirely (CSS, meta tags, etc.)
    text = re.sub(r'<head[^>]*>.*?</head>', '', text, flags=re.DOTALL | re.IGNORECASE)

    # Remove <style>...</style> blocks
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)

    # Remove <script>...</script> blocks
    text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL | re.IGNORECASE)

    # Remove HTML comments
    text = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)

    # Remove conditional comments (<!--[if ...]>...<![endif]-->)
    text = re.sub(r'<!\[if[^\]]*\]>.*?<!\[endif\]>', '', text, flags=re.DOTALL | re.IGNORECASE)

    # Convert <h2> to === SECTION NAME ===
    def _h2_replace(m):
        inner = re.sub(r'<[^>]+>', '', m.group(1)).strip()
        return f"\n\n=== {inner.upper()} ===\n"
    text = re.sub(r'<h2[^>]*>(.*?)</h2>', _h2_replace, text, flags=re.DOTALL | re.IGNORECASE)

    # Convert <h1> to a prominent header
    def _h1_replace(m):
        inner = re.sub(r'<[^>]+>', '', m.group(1)).strip()
        return f"\n{'=' * 60}\n  {inner}\n{'=' * 60}\n"
    text = re.sub(r'<h1[^>]*>(.*?)</h1>', _h1_replace, text, flags=re.DOTALL | re.IGNORECASE)

    # Convert <h3> to --- Section ---
    def _h3_replace(m):
        inner = re.sub(r'<[^>]+>', '', m.group(1)).strip()
        return f"\n--- {inner} ---\n"
    text = re.sub(r'<h3[^>]*>(.*?)</h3>', _h3_replace, text, flags=re.DOTALL | re.IGNORECASE)

    # Convert <a href="url">text</a> to text (url)
    def _link_replace(m):
        url = m.group(1).strip()
        link_text = re.sub(r'<[^>]+>', '', m.group(2)).strip()
        # Skip mailto links and anchors
        if url.startswith('#') or url.startswith('mailto:'):
            return link_text
        # If link text is the same as URL, just show URL
        if link_text == url or not link_text:
            return url
        return f"{link_text} ({url})"
    text = re.sub(r'<a[^>]+href="([^"]*)"[^>]*>(.*?)</a>', _link_replace, text, flags=re.DOTALL | re.IGNORECASE)

    # Convert <li> to   - item
    def _li_replace(m):
        inner = re.sub(r'<[^>]+>', '', m.group(1)).strip()
        return f"\n  - {inner}"
    text = re.sub(r'<li[^>]*>(.*?)</li>', _li_replace, text, flags=re.DOTALL | re.IGNORECASE)

    # Convert <br> and <br/> to newlines
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)

    # Convert </p>, </div>, </tr> to newlines (block-level endings)
    text = re.sub(r'</p>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</div>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</tr>', '\n', text, flags=re.IGNORECASE)

    # Convert <td> separators to " | " for table readability
    text = re.sub(r'<td[^>]*>', ' | ', text, flags=re.IGNORECASE)

    # Convert <hr> to a separator line
    text = re.sub(r'<hr[^>]*/?>', '\n' + '-' * 50 + '\n', text, flags=re.IGNORECASE)

    # Strip all remaining HTML tags
    text = re.sub(r'<[^>]+>', '', text)

    # Decode common HTML entities
    entity_map = {
        '&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"',
        '&apos;': "'", '&nbsp;': ' ', '&mdash;': '--', '&ndash;': '-',
        '&middot;': '*', '&bull;': '*', '&ldquo;': '"', '&rdquo;': '"',
        '&lsquo;': "'", '&rsquo;': "'", '&hellip;': '...',
        '&copy;': '(c)', '&reg;': '(R)', '&trade;': '(TM)',
        '&#9650;': '^', '&#9660;': 'v', '&#8594;': '->', '&#8592;': '<-',
        '&#x2022;': '*',
    }
    for entity, char in entity_map.items():
        text = text.replace(entity, char)

    # Decode numeric HTML entities (&#NNN; and &#xHHH;)
    def _decode_numeric(m):
        try:
            if m.group(1):
                return chr(int(m.group(1)))
            elif m.group(2):
                return chr(int(m.group(2), 16))
        except (ValueError, OverflowError):
            return m.group(0)
        return m.group(0)
    text = re.sub(r'&#(\d+);|&#x([0-9a-fA-F]+);', _decode_numeric, text)

    # Collapse multiple blank lines to at most two
    text = re.sub(r'\n{4,}', '\n\n\n', text)

    # Collapse multiple spaces on a line (but preserve leading whitespace for list items)
    text = re.sub(r'[^\S\n]{3,}', '  ', text)

    # Clean up lines: strip trailing whitespace per line
    lines = [line.rstrip() for line in text.split('\n')]
    text = '\n'.join(lines)

    # Remove leading blank lines
    text = text.lstrip('\n')

    # Ensure trailing newline
    if not text.endswith('\n'):
        text += '\n'

    return text


def send(html: str, re_line: Optional[str] = None, subject: Optional[str] = None,
         recipients: Optional[list] = None):
    """
    Send the digest HTML via Gmail SMTP.
    Required environment variables:
      GMAIL_USER      — Gmail address (used for SMTP auth)
      GMAIL_APP_PASS  — 16-char Gmail App Password
      DIGEST_TO       — comma-separated recipient list
    Optional:
      GMAIL_FROM      — sending alias (defaults to GMAIL_USER)
    """
    gmail_user = os.environ.get("GMAIL_USER")
    gmail_pass = os.environ.get("GMAIL_APP_PASS")
    if not gmail_user or not gmail_pass:
        raise RuntimeError("Missing GMAIL_USER or GMAIL_APP_PASS environment variables")
    from_addr = os.environ.get("GMAIL_FROM", gmail_user)
    to_str = os.environ.get("DIGEST_TO", gmail_user)

    if recipients is None:
        recipients = [r.strip() for r in to_str.split(",") if r.strip()]

    if subject is None:
        from zoneinfo import ZoneInfo
        date_str = datetime.now(ZoneInfo("America/New_York")).strftime("%m/%d/%Y")
        if re_line:
            # Truncate RE: line for subject (max ~120 chars total)
            max_re = 100
            re_short = re_line[:max_re] + ("..." if len(re_line) > max_re else "")
            subject = f"Korea Daily Brief · {date_str} — {re_short}"
        else:
            subject = f"Korea Daily Brief · {date_str}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"CSIS Korea Chair <{from_addr}>"
    msg["To"] = from_addr
    # BCC recipients are NOT added as a header — they are passed only to
    # sendmail() so they receive the email without being visible to others.

    plain = _html_to_plain_text(html)
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html, "html"))

    print(f"\n📨  Sending digest (BCC) to: {', '.join(recipients)}")
    max_retries = 3
    for attempt in range(max_retries):
        try:
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
                server.login(gmail_user, gmail_pass)
                server.sendmail(gmail_user, recipients, msg.as_string())
            print(f"  ✅  Sent: {subject}")
            return
        except smtplib.SMTPAuthenticationError as e:
            print(f"  ✗  Gmail auth failed: {e}")
            print("     Check GMAIL_USER and GMAIL_APP_PASS (use a 16-char App Password)")
            raise
        except (smtplib.SMTPException, OSError) as e:
            if attempt < max_retries - 1:
                wait = (attempt + 1) * 5
                print(f"  ⚠  SMTP error (retry {attempt + 1}/{max_retries} in {wait}s): {e}")
                time.sleep(wait)
            else:
                print(f"  ✗  SMTP failed after {max_retries} attempts: {e}")
                raise


if __name__ == "__main__":
    html = Path("latest.html").read_text(encoding="utf-8")
    send(html)
