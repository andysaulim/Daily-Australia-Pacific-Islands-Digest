"""Render a representative brief to preview.html for the visual check.

test_render_visual.py measures computed style in a real browser — contrast,
typeface count, horizontal overflow — and none of that is visible by reading
the HTML. It needs a page to measure, and nothing produced one, so the check
existed and never ran.

This builds a digest that exercises every section, so the measurement covers
the whole brief rather than whatever happened to be in the news.

    python3 preview.py && BRIEF_HTML=preview.html python3 test_render_visual.py
"""
import pathlib
import render

DIGEST = {'also_today': [{'body_text': 'Appointment effective October.',
                 'category': 'pacific',
                 'headline': 'PIF secretariat names new deputy',
                 'source': 'Islands Business',
                 'url': 'https://example.org/6'},
                {'body_text': 'Japan and Korea led demand.',
                 'category': 'trade',
                 'headline': 'Beef export volumes rise',
                 'source': 'AFR',
                 'url': 'https://example.org/7'}],
 'calendar_watch': [{'confirmed': True,
                     'date': '2026-09-12',
                     'day': 12,
                     'detail': 'First since the pact.',
                     'event': 'PIF leaders meet',
                     'headline': 'PIF leaders meet',
                     'month': 'Sep',
                     'why_it_matters': 'First since the pact.'}],
 'key_stat': {'context': 'Restated in Senate estimates, unchanged since 2023.',
              'label': 'AUKUS submarine programme cost to 2055',
              'number': '$368B',
              'source': 'Department of Defence'},
 'market_indicators': {'asx200': {'change_pct': 0.5, 'value': '8,142.30'},
                       'aud_usd': {'change_pct': -0.2, 'value': '0.6612'},
                       'brent': {'change_pct': -1.1, 'value': '72.40'}},
 'morning_memo': ['The Osborne yard milestone slipped by *six months*.',
                  '**Richard Marles** arrived in Honiara.',
                  'The RBA held the cash rate at 3.60 percent.'],
 'overnight_items': [{'body_text': 'Third this year.',
                      'category': 'Security',
                      'headline': 'Patrol boat handed to Solomon Islands',
                      'source': 'RNZ Pacific',
                      'url': 'https://example.org/3'},
                     {'body_text': 'Two portfolios change hands.',
                      'category': 'Pacific',
                      'headline': 'Vanuatu cabinet reshuffle',
                      'source': 'Vanuatu Daily Post',
                      'url': 'https://example.org/4'},
                     {'body_text': 'Covers three projects.',
                      'category': 'Trade',
                      'headline': 'Critical minerals MOU signed',
                      'source': 'The Australian',
                      'url': 'https://example.org/5'}],
 'pdf_url': 'https://example.org/digest_2026-09-09.pdf',
 're_line': 'AUKUS milestone slips · Marles in Honiara · RBA holds',
 'top_stories': [{'body': 'The submarine construction yard will miss its first '
                          'milestone, **Richard Marles** confirmed.',
                  'headline': 'AUKUS Osborne milestone slips six months',
                  'source': 'AFR',
                  'url': 'https://example.org/1'},
                 {'body': 'The board held at 3.60 percent, citing services inflation.',
                  'headline': 'RBA holds the cash rate',
                  'source': 'ABC News',
                  'url': 'https://example.org/2'}],
 'web_url': 'https://andysaulim.github.io/Daily-Australia-Pacific-Islands-Digest/latest.html'}

if __name__ == "__main__":
    html = render.render(dict(DIGEST))
    pathlib.Path("preview.html").write_text(html, encoding="utf-8")
    print(f"preview.html written ({len(html):,} chars)")
