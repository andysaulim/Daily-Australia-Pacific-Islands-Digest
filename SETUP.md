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
| GitHub personal access token | github.com → Settings → Developer settings → Personal access tokens | Scopes: `repo` and `workflow`. Needed **only** for the external cron in step 8, which fires from outside Actions and so cannot use the built-in token. |
| Sender Gmail account | n/a | Whatever address the brief should come from. Reuse the Korea/Japan sender if the Chair is happy with that. |

Do not paste any of these into a file in the repo. They go into GitHub Secrets in
step 5, and into your shell only for local test runs.

---

## 2. Populate the three seeded files

**Do this before the first send, not after.** Three files ship with placeholder
content. They are marked so the model refuses to state them as fact, which means
the brief will simply be quieter than it should be until they are filled in. Each
line needs two independent working sources, per the house sourcing gate.

1. **`digest.py` → `_REGIONAL_BASELINES`**: **populated, but verify before the
   first send.** It now carries the AU and NZ ministries, the 2025 House result,
   the defence spending trajectory on both measures, the RBA cash rate, and the
   Pacific set. Every value was gathered by web search from a machine that could
   not open the cited pages, which is weaker than the house gate.
   `BASELINES_WORKSHEET.md` opens with a ten-minute pass: five primary URLs,
   confirm the names, then delete that section. Three lines are deliberately
   absent rather than guessed, the RBNZ cash rate among them.
2. **`data/aukus_tracker.json`**: every Pillar 1 milestone currently marked
   `"confidence": "seed"`. The seed ledger is already in the repo; regenerate it
   at any time with `python aukus_tracker.py`, which writes the file if it is
   missing and prints the block the prompt will see. Change `"confidence"` to
   `"confirmed"` only for lines you have actually sourced twice.
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

Two hundred and fourteen offline checks, no network and no API spend. All should pass.

Then a real generation, no email:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
python run.py --no-send
```

Open `latest.html`. Check the word count clears 1,400, the Pacific and New Zealand
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

**Already done.** The repository is
[`andysaulim/Daily-Australia-Pacific-Islands-Digest`](https://github.com/andysaulim/Daily-Australia-Pacific-Islands-Digest),
and the pipeline, the workflow, and the seeded tracker ledgers are on `main`.
Clone it rather than re-initialising:

```bash
git clone https://github.com/andysaulim/Daily-Australia-Pacific-Islands-Digest.git
cd Daily-Australia-Pacific-Islands-Digest
```

If you are pushing further work from the laptop, check `git status` before
committing. `__pycache__/`, `collected.json`, `digest.json`, `latest.html`, and
`smoke_output.html` should be absent, they are gitignored. Everything under
`data/` is tracked on purpose: `archive.db` and the tracker JSON are the
cross-day memory, and the workflow commits them back after every run.

---

## 5. Add the secrets

Repo → **Settings** → **Secrets and variables** → **Actions** → **New repository
secret**. Five, and only five:

| Name | Value |
| --- | --- |
| `ANTHROPIC_API_KEY` | from step 1 |
| `GMAIL_USER` | the sender address |
| `GMAIL_APP_PASS` | the 16-character app password |
| `DIGEST_TO` | **your address only, for now.** Comma-separated when you widen it. |
| `ALERT_TO` | your address. Failure alerts must never reach the distribution list. |

**`GH_PAT` is deliberately not in that table.** Nothing in either workflow
reads it: the Pages deploy and the double-send guard both run on the built-in
`GITHUB_TOKEN`. The step 8 token lives at cron-job.org and nowhere else. A
token stored as a repository secret that nothing reads is exposure without
benefit, so do not add it here.

`DIGEST_TO` starting as just you is the whole point of the pilot in step 8. Widen
it deliberately, not on day one.

---

## 5b. Newsletters over IMAP (optional, off by default)

`newsletters.py` reads subscriber-only newsletters out of the inbox that receives
them. For a newsletter you pay for there is no wall to defeat: the publisher
mailed it to you, and the email HTML carries the links and blurbs. That reaches
the briefings with editorial judgement already applied, which no public feed does.

It is **off** unless you turn it on, because an always-on IMAP login with nothing
subscribed would print a failure line every morning until you did.

1. Subscribe the `GMAIL_USER` inbox to the newsletters you want ingested.
2. Check IMAP on that account: Gmail **Settings** -> **Forwarding and
   POP/IMAP**. Google made IMAP always-on for personal Gmail, so that page
   now shows only the sub-settings (auto-expunge, folder size) and no
   enable/disable control. If you see no toggle, there is nothing to turn
   on. Some Workspace accounts still show one, under admin control. POP is
   irrelevant either way; the pipeline does not use it.
3. Add sender or subject fingerprints to `_PUBLISHERS` / `_NEWSLETTERS` in
   `newsletters.py`. It ships with plausible candidates for this beat; an
   unsubscribed one simply never matches.

   **Politico Canberra Playbook is the one worth doing first.** It is the
   only daily on Australian federal politics any of the named international
   outlets runs, and it is a subscriber product: the Google News feed
   catches free editions at best, while the mailed issue carries the whole
   thing with the editing already done. Subscribe at
   politico.com/newsletters/canberra-playbook with the `GMAIL_USER`
   address; the `canberra playbook` fingerprint is already in place.
4. Add the repository *variable* `NEWSLETTERS` = `1`. **Set as of 25 August
   2026**, so the IMAP path is live. The Actions **Variables** tab is the
   authority on this, not any file in the repo.

No new secret: it reuses `GMAIL_USER` and `GMAIL_APP_PASS`. Items land in tier 1
already region-tagged and pass the same relevance and sport gates as feed items,
so a masthead briefing cannot smuggle the football in.

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
| `WEB_URL` | `https://andysaulim.github.io/Daily-Australia-Pacific-Islands-Digest` |

Leave `WEB_URL` unset and the read-online bar simply does not render.

---

## 7. First run, by hand

Repo → **Actions** → **Australia Chair Daily Brief** → **Run workflow** → **Run
workflow**.

Watch the log. What to look for:

- **Source health**: how many of the 121 feeds returned data. This is the first
  honest reading you will get, since your laptop's number is meaningless. Under
  about 70 means something is wrong; a handful of dead feeds is normal.
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

**Not optional. Do this one.** GitHub's own scheduler has no SLA, and on this
repository it has been far worse than the 30 minutes to 4 hours the Korea and
Japan briefs budget for:

| Date | 7:30 AM ET slot | 9:00 AM ET slot |
| --- | --- | --- |
| 26 Aug | 22 min late | 54 min late |
| 27 Aug | **9.6 h late**, fired 5:03 PM | **9.8 h late**, fired 6:50 PM |
| 28 Aug | 3 h overdue, had not fired | 1.5 h overdue, had not fired |

The workflow now spreads six slots between 6:00 and 9:00 AM ET so one late slot
does not decide the day, but spreading attempts cannot make GitHub punctual. A
brief that lands at 6:50 PM is not a morning brief, and worse, it writes those
stories into the cross-day memory so the next morning's issue cannot re-run
them. The external cron is the only thing that actually fixes this.

### 8a. The token

This is the one place a personal access token is genuinely needed: the dispatch
fires from outside Actions, so it cannot use the built-in `GITHUB_TOKEN`.

**Use a fine-grained token, not a classic one.** github.com -> Settings ->
Developer settings -> Personal access tokens -> Fine-grained tokens ->
Generate new token:

- **Repository access:** Only select repositories ->
  `Daily-Australia-Pacific-Islands-Digest`
- **Permissions:** Repository permissions -> **Actions: Read and write**. That
  is the only one. Metadata read-only is added automatically and cannot be
  removed.
- **Expiration:** set a reminder for the day before. See 8c.

A classic token would work, but every classic scope that can dispatch a
workflow is far wider than this job needs: `repo` grants read and write to the
contents of every private repository on the account, to trigger one public
workflow. If you must use a classic token, this repository is public, so
`public_repo` alone is enough. `workflow` is NOT needed: that scope is for
pushing changes to workflow files, not for running them.

### 8b. The job

At cron-job.org (free, and what the Korea brief uses), create a job:

- **URL:** `https://api.github.com/repos/andysaulim/Daily-Australia-Pacific-Islands-Digest/actions/workflows/daily-brief.yml/dispatches`
- **Method:** POST
- **Headers:**
  - `Authorization: Bearer <the token from 8a>`
  - `Accept: application/vnd.github+json`
  - `Content-Type: application/json`
- **Body:** `{"ref":"main"}`
- **Schedule:** 06:00 America/New_York, Monday to Friday

`{"ref":"main"}` is the whole body. The workflow's one input, `replace_today`,
defaults to false, so omitting it is correct for the daily send.

Test it before you trust it. From a terminal, with the token in a shell
variable rather than typed into the command where it lands in your history:

```bash
read -rs GH_PAT                     # paste the token, press enter
curl -i -X POST \
  -H "Authorization: Bearer $GH_PAT" \
  -H "Accept: application/vnd.github+json" \
  https://api.github.com/repos/andysaulim/Daily-Australia-Pacific-Islands-Digest/actions/workflows/daily-brief.yml/dispatches \
  -d '{"ref":"main"}'
```

**204 No Content** means it fired; check Actions for a new run. **401** is a bad
or expired token. **403** is a token without Actions write. **404** usually
means the token cannot see the repository, not that the workflow is missing.

### 8c. When it expires

A fine-grained token expires, and when it does the dispatch starts returning
401 and simply stops running the brief. Nothing in this repository can detect
that: from the workflow's side an absent dispatch is indistinguishable from a
quiet morning.

Two things make it visible. Turn on cron-job.org's failure notifications, which
email you when a job stops returning 204. And put the expiry date in your
calendar with a day's warning. The six Actions crons keep the brief alive
meanwhile, at whatever hour GitHub feels like, which is the situation this step
exists to end.

---

## 9. Two-week supervised pilot

Keep `DIGEST_TO` as yourself and the Chair. Each morning, check:

| What | Where | What it means |
| --- | --- | --- |
| Repetition across days | Read Monday against Friday | The cross-day memory is the reason this brief exists in its current form. This is the check that matters most. |
| Pacific stand-in frequency | The README stats block | Three or more in ten issues and the Pacific feed set needs sources, not a lower floor. The README says so itself. |
| Word count | README stats | Consistently near 1,400 means the feeds are starving the brief. |
| Validation retries | README stats | Frequent retries mean a cap or floor is set wrong for this region's actual volume. |
| Dead feeds | Actions log | Move anything returning 403 to Google News routing. Never delete it outright. |

`SOURCES.md` lists every feed with its tier, region and routing, so you can
check a dead one against what it was meant to be doing without opening
`collect.py`. Regenerate it with `python list_sources.py` after any feed
change; the smoke suite fails if the committed copy has gone stale.

Tune `SECTION_CAPS` in `run.py` and the feed dicts in `collect.py` from what you
see. Widen `DIGEST_TO` only once a fortnight of issues would have been fit to send.

---

## 10. After the pilot

- **Friday Week in Review.** Built: `weekly.py` on its own Friday workflow,
  reading `data/archive.db`. It has nothing to synthesise until there is a week
  of issues behind it, so judge it after the pilot, not on its first Friday.
- **Market strip.** Built: ASX 200, AUD/USD, NZX 50, NZD/USD and Brent, Yahoo
  with a Stooq fallback, rendered only for what actually resolved. Iron ore is
  deliberately absent: Yahoo's `TIO=F` stopped updating and returns quotes over
  1,800 days old, which `markets.py` correctly refuses. If you find a free
  endpoint with a current 62% Fe CFR price, add it to `INDICATORS` with its
  sanity range; do not restore `TIO=F`.
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
