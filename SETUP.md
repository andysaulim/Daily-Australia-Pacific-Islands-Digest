# Setup: Australia Chair Daily Brief

Everything needed to take this from a folder on your laptop to a brief that sends
itself every weekday. Work top to bottom; each step assumes the ones above it.

`gh` (the GitHub CLI) is not installed on this machine, so the repo and secrets
steps go through the web UI. The Actions runner has `gh` preinstalled, which is
all the double-send guard needs.

---

## 1. Credentials to collect first

Four things, gathered before you touch GitHub. Three are new; one you may already
have from the Korea or Japan brief.

| What | Where | Notes |
| --- | --- | --- |
| Anthropic API key | console.anthropic.com → API keys | The Korea brief's key works if you would rather share billing. A separate key makes the Australia Chair's spend legible on its own. |
| Gmail app password | myaccount.google.com → Security → 2-Step Verification → App passwords | 16 characters, **not** your account password. Requires 2FA on the account. Same drill as the Korea brief. |
| GitHub personal access token | github.com → Settings → Developer settings → Personal access tokens | Scopes: `repo` and `workflow`. Used for the Pages deploy and the guard's `gh run list`. |
| Sender Gmail account | n/a | Whatever address the brief should come from. Reuse the Korea/Japan sender if the Chair is happy with that. |

Do not paste any of these into a file in the repo. They go into GitHub Secrets in
step 5, and into your shell only for local test runs.

---

## 2. Populate the three seeded files

**Do this before the first send, not after.** Three files ship with placeholder
content. They are marked so the model refuses to state them as fact, which means
the brief will simply be quieter than it should be until they are filled in. Each
line needs two independent working sources, per the house sourcing gate.

1. **`digest.py` → `_REGIONAL_BASELINES`**: the AU and NZ ministries and portfolio
   holders, defence spending trajectory, RBA cash rate and RBNZ OCR, PIF membership
   and current chair, which states hold Compacts of Free Association, which
   recognise Taiwan. The file lists exactly what to fill in.
2. **`data/aukus_tracker.json`**: every Pillar 1 milestone currently marked
   `"confidence": "seed"`. Generate the file first by running
   `python aukus_tracker.py`, then edit it. Change `"confidence"` to `"confirmed"`
   only for lines you have actually sourced twice.
3. **The calendar**: add confirmed dates with
   `python -c "import calendar_tracker as c; c.add_event('AUSMIN 2026', '2026-11-12', ['url1','url2'])"`.
   `add_event()` refuses anything with fewer than two sources, so you cannot
   accidentally seed an unsourced date.

An out-of-date minister in the baselines is worse than an empty baseline, because
the model trusts what this block tells it.

---

## 3. Local test run

Prove it works on your machine before GitHub is involved.

```bash
cd "C:/Users/ALim/Dropbox/Australia Newsletter"
pip install -r requirements.txt
python smoke_test.py
```

Fifty offline checks, no network and no API spend. All should pass.

Then a real generation, no email:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
python run.py --no-send
```

Open `latest.html`. Check the word count clears 850, the Pacific and New Zealand
sections have real items or an honest stand-in line, and nothing looks fabricated.

**Expect the feed count to look bad on the CSIS network.** It intercepts TLS and
throttles bulk RSS: a first run pulled 23 of 105 feeds, later runs 3. That is the
network, not the code. GitHub's runners have no middlebox. Do not tune the feed
list based on what you see locally.

---

## 4. Create the repo and push

Set your git identity first, it is not currently configured on this machine:

```bash
git config --global user.name "Andy Lim"
git config --global user.email "andysaulim@gmail.com"
```

Create the repository on github.com: **New repository** → name it
`australia-chair-daily-brief` → **Private** → do not add a README, .gitignore, or
licence (the repo already has them).

Then, from the project folder:

```bash
cd "C:/Users/ALim/Dropbox/Australia Newsletter"
git init -b main
git add .
git status
```

Check `git status` before committing. `_reference/` (the Korea snapshot) and
`__pycache__/` should be absent, they are gitignored. `data/.gitkeep` and
`public/.gitkeep` should be present.

```bash
git commit -m "Australia Chair Daily Brief pipeline v1"
git remote add origin https://github.com/<your-username>/australia-chair-daily-brief.git
git push -u origin main
```

---

## 5. Add the secrets

Repo → **Settings** → **Secrets and variables** → **Actions** → **New repository
secret**. Six entries:

| Name | Value |
| --- | --- |
| `ANTHROPIC_API_KEY` | from step 1 |
| `GMAIL_USER` | the sender address |
| `GMAIL_APP_PASS` | the 16-character app password |
| `DIGEST_TO` | **your address only, for now.** Comma-separated when you widen it. |
| `ALERT_TO` | your address. Failure alerts must never reach the distribution list. |
| `GH_PAT` | the personal access token from step 1 |

`DIGEST_TO` starting as just you is the whole point of the pilot in step 8. Widen
it deliberately, not on day one.

---

## 6. GitHub Pages (optional)

The archive gives the email its "Read online" link and lets the Chair browse back
issues. Skip it if an internal working product should not have a public URL; the
brief sends fine without it, and `data/archive.db` keeps every issue regardless.

To enable: repo → **Settings** → **Pages** → Source: **Deploy from a branch** →
branch `gh-pages`, folder `/`. The branch does not exist until the first run
creates it, so set this after step 7.

Then add a repository *variable* (the **Variables** tab, not Secrets):

| Name | Value |
| --- | --- |
| `WEB_URL` | `https://<your-username>.github.io/australia-chair-daily-brief` |

Leave `WEB_URL` unset and the read-online bar simply does not render.

---

## 7. First run, by hand

Repo → **Actions** → **Australia Chair Daily Brief** → **Run workflow** → **Run
workflow**.

Watch the log. What to look for:

- **Source health**: how many of the 105 feeds returned data. This is the first
  honest reading you will get, since your laptop's number is meaningless. Under
  about 60 means something is wrong; a handful of dead feeds is normal.
- **Regional balance**: the AU / NZ / Pacific counts. If Pacific is under 5, the
  Pacific section will lean on its stand-in and the feed set needs sources.
- **Validation**: passed, or which gate failed and whether the retry cleared it.
- The email arriving.

Then open it in Gmail, Outlook, and Apple Mail, desktop and mobile, light and
dark. Outlook is where table layouts break. This is the one check nothing
automated covers.

Run it a second time the same day to confirm the double-send guard: the fallback
crons should skip once a dispatch has succeeded.

---

## 8. The external cron

GitHub's own scheduler has no SLA and drifts 30 minutes to 4 hours, which is why
both the Korea and Japan briefs trigger from outside and keep the Actions crons
only as a safety net.

At cron-job.org (free, and what the Korea brief uses), create a job:

- **URL:** `https://api.github.com/repos/<user>/australia-chair-daily-brief/actions/workflows/daily-brief.yml/dispatches`
- **Method:** POST
- **Headers:**
  - `Authorization: Bearer <your GH_PAT>`
  - `Accept: application/vnd.github+json`
  - `Content-Type: application/json`
- **Body:** `{"ref":"main"}`
- **Schedule:** 06:00 America/New_York, Monday to Friday

A 204 back means it fired. The two Actions crons at 7:30 and 9:00 AM ET stay as
they are; the guard stops them duplicating a successful dispatch.

---

## 9. Two-week supervised pilot

Keep `DIGEST_TO` as yourself and the Chair. Each morning, check:

| What | Where | What it means |
| --- | --- | --- |
| Repetition across days | Read Monday against Friday | The cross-day memory is the reason this brief exists in its current form. This is the check that matters most. |
| Pacific stand-in frequency | The README stats block | Three or more in ten issues and the Pacific feed set needs sources, not a lower floor. The README says so itself. |
| Word count | README stats | Consistently near 850 means the feeds are starving the brief. |
| Validation retries | README stats | Frequent retries mean a cap or floor is set wrong for this region's actual volume. |
| Dead feeds | Actions log | Move anything returning 403 to Google News routing. Never delete it outright. |

Tune `SECTION_CAPS` in `run.py` and the feed dicts in `collect.py` from what you
see. Widen `DIGEST_TO` only once a fortnight of issues would have been fit to send.

---

## 10. After the pilot

- **Friday Week in Review.** The Korea brief has `weekly.py`, synthesising the
  week's archived issues on a separate Friday schedule. Deliberately not built
  yet: it needs a corpus of real issues to synthesise, so it is worth writing
  after the pilot rather than before.
- **Market strip.** Left out of v1 by choice. The render slot and the
  `market_indicators` key are stubbed, so ASX 200, AUD/USD, NZX 50, NZD/USD, iron
  ore, the RBA cash rate and the RBNZ OCR can drop in without a re-layout.
- **Structured outputs.** `digest.py` asks for bare JSON and cleans up the result
  with `_robust_json_parse`. The current API can guarantee valid JSON via
  `output_config.format` with a schema. Worth adopting once there is a key to test
  a schema that large against.

---

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `400` on the first Claude call | A reintroduced assistant prefill | The current models reject prefills. `digest.py` deliberately does not use one; the Korea pipeline does, so do not copy that pattern back in. |
| Auth error at send | App password rotated or expired | Regenerate it, update `GMAIL_APP_PASS`. |
| Two emails in one day | Guard bypassed | Check the "Check if already sent today" step and that the `--workflow` name in `daily-brief.yml` still matches the workflow's `name:`. |
| Pages step red, email arrived | Post-send step failed | Expected and non-fatal. Every post-send step is `continue-on-error` precisely so this does not mark the run failed and let a fallback cron send a second copy. |
| Repeated regeneration, then no send | Validation could not be satisfied | Usually a thin news day or a starved collector. Check source health first, then the caps. |
| Pacific section always the stand-in | Pacific feeds not returning | Check which Pacific sources are in the dead list and reroute them through Google News. Do not lower the floor. |
| Feeds fail locally but work in Actions | TLS interception on the CSIS network | Expected. See the note in step 3. |
