# Regional baselines: verification record

`_REGIONAL_BASELINES` in `digest.py` is now populated and live in the prompt.
This file records where each line came from and what still needs checking.
**Nothing here reaches the model.** It is not imported and not read by any code.

## Read this before the first supervised send

Every line in the block was gathered by web search, and the machine that
gathered it could not open a single cited page: the sandbox blocked egress to
everything except the search index. So the citations are search attributions,
not pages anyone read. That is weaker than the house gate, which asks for two
independent *working* sources.

It matters because it already went wrong once. A first search returned **Andrew
Charlton** as Treasurer; a targeted cross-check returned **Jim Chalmers**, with
Charlton as Cabinet Secretary. Only the cross-check caught it. Every line below
survived at least one such cross-check, but that is not the same as being read
off a primary source.

**The ten-minute pass that closes this out:** open the five primary sources in
the table below, confirm the names, and delete this section. Nothing else in the
repo depends on it.

| Open this | Confirms |
| --- | --- |
| `pmc.gov.au` current ministry list | The five Australian government names |
| [ABC, 17 Feb 2026](https://www.abc.net.au/news/2026-02-17/angus-taylor-preparing-to-unveil-shadow-ministry/106354092) | Taylor, Hume, Wilson, O'Brien, Paterson |
| `dpmc.govt.nz` ministerial list | Luxon, Peters, Collins |
| [RBA, 11 Aug 2026](https://www.rba.gov.au/media-releases/2026/mr-26-19.html) | Cash rate 4.35% |
| [forumsec.org](https://forumsec.org/pacific-islands-forum) | 18 members, chair, CoFA states |

## What went into the block

| Area | Status | Basis |
| --- | --- | --- |
| AU government, five names | In | Ministerial sites, corroborated by August 2026 activity |
| AU opposition, five names | In | [ABC](https://www.abc.net.au/news/2026-02-17/angus-taylor-preparing-to-unveil-shadow-ministry/106354092), [Paterson media release](https://www.senatorpaterson.com.au/news/media-release-appointment-as-shadow-minister-for-defence-17-february-2026), Defence Connect |
| AU parliament, seats and next election | In | 2025 House result (Labor 94 of 150); [AEC, Jul 2026](https://www.aec.gov.au/media/2026/07-23.htm) |
| NZ, four names plus election date | In | [beehive.govt.nz](https://www.beehive.govt.nz/release/general-election-be-held-7-november), [elections.nz](https://elections.nz/media-and-news/2026/key-dates-for-2026-general-election), ANZMIN March 2026 |
| Defence spending, both measures | In | [ABC in charts](https://www.abc.net.au/news/2026-04-17/australia-defence-spending-in-charts-military-investment-adf/106572172), [Breaking Defense](https://breakingdefense.com/2026/04/australia-pledges-to-boost-defense-spend-to-3-of-gdp-says-us-remains-key-partner/), ASPI Cost of Defence 2026-27 |
| RBA cash rate | In | [RBA media release](https://www.rba.gov.au/media-releases/2026/mr-26-19.html), [SBS](https://www.sbs.com.au/news/live-blog/rba-august-2026-interest-rates-decision-live/trlncz8i2) |
| RBNZ OCR | **Left out** | The search contradicted itself: an [RNZ headline](https://www.rnz.co.nz/news/business/596494/rbnz-leaves-official-cash-rate-unchanged-at-2-point-25-percent) says the OCR was held at 2.25%, while the same search summarised a July 2026 rise to 2.50%. Both cannot be current, so neither went in. |
| PIF membership, chair, CoFA, Taiwan | In | [forumsec.org](https://forumsec.org/pacific-islands-forum), [Troika release](https://forumsec.org/publications/release-forum-troika-leaders-meet-brisbane), [Focus Taiwan](https://focustaiwan.tw/politics/202602240013) |

### Deliberately left out

Three lines the file asks for could not be confirmed twice and are absent rather
than guessed:

- **RBNZ official cash rate.** The one line where two search results actively
  contradicted each other, 2.25% held versus 2.50% after a July rise. Source:
  `rbnz.govt.nz`. The block tells the model to write around it meanwhile.
- **Australia's top export markets and China's share.** Source: DFAT trade
  statistics.
- **NZ defence capability plan status.** Source: `defence.govt.nz`. Note that NZ
  is reported open to an Australia–Fiji defence arrangement, which is the live
  angle if you add this.

## Why the defence line names its measure

Australian defence spending is quoted two ways and they differ by most of a
percentage point: roughly 2% of GDP on the conventional Australian measure,
roughly 2.8% on the NATO-style measure the 2026 NDS uses. Commentary mixes them
freely. The block tells the model to say which one it means, because a brief
that reports "2%" one day and "2.8%" the next looks wrong even when both are
right.

## Review dates

| When | What goes stale |
| --- | --- |
| 1 Sep 2026 | PIF chair passes to Whipps during the Koror meeting |
| 2 Sep 2026 | Next RBNZ decision |
| 7 Nov 2026 | NZ general election. Every NZ name becomes a caretaker question |
| Every ~6 weeks | RBA cash rate |
| On any reshuffle | Both Australian ministry blocks |

The PIF and NZ election dates are both in `data/calendar.json` as confirmed
entries, so `calendar_watch` will surface them as they approach. That is the
closest thing to an automatic reminder this repo has.
