"""
Australia Chair Daily Brief: Email Sender
CSIS Australia Chair

Gmail SMTP over SSL with an app password. Recipients are passed to sendmail()
only, never written into a header, so the distribution list stays private.

Carried over near-verbatim from the Korea Daily Brief sender.
"""
import os
import re
import smtplib
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from pathlib import Path
from typing import Optional

BRIEF_NAME = "Australia Chair Daily Brief"
SENDER_LABEL = "CSIS Australia Chair"


# ─────────────────────────────────────────────────────────────────────────────
# HTML -> plain text
# ─────────────────────────────────────────────────────────────────────────────

def _html_to_plain_text(html: str) -> str:
    """Convert the brief's HTML into readable plain text.

    Regex only, no HTML parser dependency. The output should be readable in a
    terminal or a text-only mail client.
    """
    text = html

    text = re.sub(r"<head[^>]*>.*?</head>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    text = re.sub(r"<!\[if[^\]]*\]>.*?<!\[endif\]>", "", text, flags=re.DOTALL | re.IGNORECASE)

    def _h1(m):
        inner = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        return f"\n{'=' * 60}\n  {inner}\n{'=' * 60}\n"
    text = re.sub(r"<h1[^>]*>(.*?)</h1>", _h1, text, flags=re.DOTALL | re.IGNORECASE)

    def _h2(m):
        inner = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        return f"\n\n=== {inner.upper()} ===\n"
    text = re.sub(r"<h2[^>]*>(.*?)</h2>", _h2, text, flags=re.DOTALL | re.IGNORECASE)

    def _h3(m):
        inner = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        return f"\n--- {inner} ---\n"
    text = re.sub(r"<h3[^>]*>(.*?)</h3>", _h3, text, flags=re.DOTALL | re.IGNORECASE)

    def _link(m):
        url = m.group(1).strip()
        label = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        if url.startswith("#") or url.startswith("mailto:"):
            return label
        if label == url or not label:
            return url
        return f"{label} ({url})"
    text = re.sub(r'<a[^>]+href="([^"]*)"[^>]*>(.*?)</a>', _link, text,
                  flags=re.DOTALL | re.IGNORECASE)

    def _li(m):
        inner = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        return f"\n  - {inner}"
    text = re.sub(r"<li[^>]*>(.*?)</li>", _li, text, flags=re.DOTALL | re.IGNORECASE)

    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</div>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</tr>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<td[^>]*>", " | ", text, flags=re.IGNORECASE)
    text = re.sub(r"<hr[^>]*/?>", "\n" + "-" * 50 + "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)

    entity_map = {
        "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&apos;": "'",
        "&nbsp;": " ", "&mdash;": "-", "&ndash;": "-", "&middot;": "*",
        "&bull;": "*", "&ldquo;": '"', "&rdquo;": '"', "&lsquo;": "'",
        "&rsquo;": "'", "&hellip;": "...", "&copy;": "(c)",
        "&#9650;": "^", "&#9660;": "v", "&#8594;": "->", "&#8593;": "^",
    }
    for entity, char in entity_map.items():
        text = text.replace(entity, char)

    def _numeric(m):
        try:
            if m.group(1):
                return chr(int(m.group(1)))
            if m.group(2):
                return chr(int(m.group(2), 16))
        except (ValueError, OverflowError):
            return m.group(0)
        return m.group(0)
    text = re.sub(r"&#(\d+);|&#x([0-9a-fA-F]+);", _numeric, text)

    text = re.sub(r"\n{4,}", "\n\n\n", text)
    text = re.sub(r"[^\S\n]{3,}", "  ", text)
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = text.lstrip("\n")
    if not text.endswith("\n"):
        text += "\n"
    return text


# ─────────────────────────────────────────────────────────────────────────────
# SEND
# ─────────────────────────────────────────────────────────────────────────────

def send(html: str, re_line: Optional[str] = None, subject: Optional[str] = None,
         recipients: Optional[list] = None):
    """Send the brief.

    Required environment:
      GMAIL_USER      Gmail address used for SMTP auth
      GMAIL_APP_PASS  16-character Gmail app password, not the account password
      DIGEST_TO       comma-separated recipient list, delivered as BCC
    Optional:
      GMAIL_FROM      sending alias, defaults to GMAIL_USER
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
            max_re = 100
            re_short = re_line[:max_re] + ("..." if len(re_line) > max_re else "")
            subject = f"{BRIEF_NAME} - {date_str} - {re_short}"
        else:
            subject = f"{BRIEF_NAME} - {date_str}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{SENDER_LABEL} <{from_addr}>"
    msg["To"] = from_addr
    # Recipients are NOT written to a header. They go to sendmail() only, so the
    # list stays private.

    msg.attach(MIMEText(_html_to_plain_text(html), "plain"))
    msg.attach(MIMEText(html, "html"))

    # Count and domains only, never the addresses. This runs in GitHub Actions,
    # and on a public repository the run log is world-readable, so printing the
    # list would publish the Chair's distribution list. The domain breakdown is
    # enough to spot "went to the wrong place" without naming anyone.
    domains = sorted({r.rsplit("@", 1)[-1] for r in recipients if "@" in r})
    print(f"\n  Sending (BCC) to {len(recipients)} recipient(s)"
          f"{' across ' + ', '.join(domains) if domains else ''}")
    max_retries = 3
    for attempt in range(max_retries):
        try:
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
                server.login(gmail_user, gmail_pass)
                server.sendmail(gmail_user, recipients, msg.as_string())
            print(f"  Sent: {subject}")
            return
        except smtplib.SMTPAuthenticationError as e:
            print(f"  x  Gmail auth failed: {e}")
            print("     Check GMAIL_USER and GMAIL_APP_PASS (a 16-character app password)")
            raise
        except (smtplib.SMTPException, OSError) as e:
            if attempt < max_retries - 1:
                wait = (attempt + 1) * 5
                print(f"  !  SMTP error (retry {attempt + 1}/{max_retries} in {wait}s): {e}")
                time.sleep(wait)
            else:
                print(f"  x  SMTP failed after {max_retries} attempts: {e}")
                raise


if __name__ == "__main__":
    send(Path("latest.html").read_text(encoding="utf-8"))
