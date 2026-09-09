# -*- coding: utf-8 -*-
"""Measure what a reader actually sees, rather than what the CSS says.

Three defects reached readers because the checks in place asserted that a rule
existed rather than that it took effect.

Dark mode: the rules were present and matched almost nothing, because they
selected div, h3 and a while the markup also uses td, p and span. Thirty-two
of 112 text nodes sat below readable contrast and several were invisible.

Typography: four faces rendered where three were intended. The fourth was
inherited by nodes that declared no font family.

Mobile: one nowrap cell in the masthead and a three-cell logo lockup in the
footer each set a min-content floor under the whole table, so the brief
scrolled sideways on a phone. Nothing in the HTML looked wrong.

All three are only visible by measuring computed style in a browser, which is
what this does. Run: python test_render_visual.py
"""
from playwright.sync_api import sync_playwright
import pathlib, collections, sys, os

SRC = pathlib.Path(os.environ.get("BRIEF_HTML", "preview.html")).resolve().as_uri()

JS = """() => {
  const lum = c => { const [r,g,b]=c.match(/\\d+/g).slice(0,3).map(Number).map(v=>{v/=255;return v<=.03928?v/12.92:Math.pow((v+.055)/1.055,2.4)});
                     return .2126*r+.7152*g+.0722*b; };
  // Composite the stack of backgrounds. A translucent overlay such as
  // rgba(255,255,255,0.04) on the navy KCNA panel is nearly navy; reading it
  // as opaque white reports a contrast failure that no reader ever sees.
  const parse = c => { const m=c.match(/[\\d.]+/g)||[]; return {r:+m[0]||0,g:+m[1]||0,b:+m[2]||0,a:m.length>3?+m[3]:1}; };
  const bgOf = el => { const stack=[]; let e=el;
    while(e){ const c=parse(getComputedStyle(e).backgroundColor);
      if(c.a>0){ stack.push(c); if(c.a>=1) break; } e=e.parentElement; }
    if(!stack.length || stack[stack.length-1].a<1) stack.push({r:255,g:255,b:255,a:1});
    let out=stack[stack.length-1];
    for(let i=stack.length-2;i>=0;i--){ const t=stack[i];
      out={r:t.r*t.a+out.r*(1-t.a), g:t.g*t.a+out.g*(1-t.a), b:t.b*t.a+out.b*(1-t.a), a:1}; }
    return `rgb(${Math.round(out.r)}, ${Math.round(out.g)}, ${Math.round(out.b)})`; };
  const out=[]; const w=document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT); let n;
  while(n=w.nextNode()){ const t=n.textContent.trim(); if(t.length<2) continue;
    const el=n.parentElement, cs=getComputedStyle(el);
    const fg=lum(cs.color), bg=lum(bgOf(el));
    out.push({t:t.slice(0,42), cr:+(((Math.max(fg,bg)+.05)/(Math.min(fg,bg)+.05)).toFixed(2)),
              color:cs.color, font:cs.fontFamily, size:cs.fontSize});
  }
  return out; }"""

# The browser sits in a different place on each host. This container
# pre-installs it at a fixed path; a GitHub runner puts it under
# ~/.cache/ms-playwright, where Playwright's own resolution finds it. Passing
# the container path on a runner raises, and this check has never once run in
# CI because of it.
_PINNED = "/opt/pw-browsers/chromium"


def _launch(p):
    if os.path.exists(_PINNED):
        return p.chromium.launch(executable_path=_PINNED)
    return p.chromium.launch()


# No browser at all is not a finding, it is a missing tool. The install step
# ahead of this one is continue-on-error for exactly that reason, so failing
# here would hold a brief over a runner hiccup rather than over the brief.
try:
    with sync_playwright() as _p:
        _launch(_p).close()
except Exception as e:
    print("SKIP: no usable Chromium, visual checks not run --", str(e).splitlines()[0][:120])
    sys.exit(0)


FLOOR = 4.5
# Widths that matter: the narrowest phone still in use, the common iPhone and
# Android sizes, the tablet breakpoints, and desktop.
WIDTHS = (320, 360, 375, 390, 414, 480, 540, 620, 700, 768, 900)
fail = False
with sync_playwright() as p:
    b = _launch(p)
    for scheme in ('light', 'dark'):
        pg = b.new_page(viewport={'width': 760, 'height': 1400}, color_scheme=scheme)
        pg.goto(SRC); pg.wait_for_timeout(250)
        rows = pg.evaluate(JS)
        bad = [r for r in rows if r['cr'] < FLOOR]
        print(f"=== {scheme}: {len(rows)} text nodes, {len(bad)} below {FLOOR}:1")
        for r in sorted(bad, key=lambda r: r['cr']):
            print(f"    {r['cr']:5}:1  {r['size']:>6}  {r['t']!r}  fg {r['color']}")
        if bad:
            fail = True
        if scheme == 'light':
            faces = collections.Counter()
            for r in rows:
                faces[r['font'].split(',')[0].strip().strip('"')] += len(r['t'])
            tot = sum(faces.values())
            print("    typefaces in use:")
            for f, ch in faces.most_common():
                print(f"      {100*ch/tot:5.1f}%  {f}")
            if len(faces) > 3:
                print(f"    !! {len(faces)} typefaces — expected at most 3")
                fail = True
        pg.close()
    b.close()
# Responsive sweep. The brief is read on a phone more often than not, and the
# failure mode is silent: one nowrap cell sets a min-content floor under the
# whole table and every section scrolls sideways.
OVERFLOW_JS = """(vw) => {
  let ov = 0;
  document.querySelectorAll('*').forEach(el => {
    const r = el.getBoundingClientRect();
    if (r.width > 0 && r.right > vw + 1) ov++;
  });
  const tiny = [];
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let n;
  while (n = w.nextNode()) {
    const t = n.textContent.trim();
    if (t.length < 3) continue;
    if (parseFloat(getComputedStyle(n.parentElement).fontSize) < 10) tiny.push(t.slice(0, 20));
  }
  return {sw: document.documentElement.scrollWidth, ov, tiny: [...new Set(tiny)]};
}"""
with sync_playwright() as p:
    b = _launch(p)
    print("\nresponsive sweep")
    for vw in WIDTHS:
        pg = b.new_page(viewport={'width': vw, 'height': 900})
        pg.goto(SRC); pg.wait_for_timeout(180)
        r = pg.evaluate(OVERFLOW_JS, vw)
        good = r['sw'] <= vw and r['ov'] == 0 and not r['tiny']
        if not good:
            fail = True
            print(f"  FAIL {vw}px  scrollWidth={r['sw']} overflowing={r['ov']} tiny={r['tiny']}")
        else:
            print(f"  ok   {vw}px")
        pg.close()
    b.close()

print("RESULT:", "FAIL" if fail else "PASS")
sys.exit(1 if fail else 0)
