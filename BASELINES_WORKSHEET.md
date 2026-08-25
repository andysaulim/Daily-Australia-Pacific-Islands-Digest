# Regional baselines: research worksheet

A staging area for `_REGIONAL_BASELINES` in `digest.py`. **Nothing here reaches the
model.** This file is not imported, not injected into the prompt, and not read by
any code. That is deliberate: a half-verified name sitting in the prompt block
gets asserted as fact whether or not a disclaimer sits above it, so the draft
lives out here until a human has checked it.

## How to use this

For each line: confirm the value against the two cited sources, then copy the
confirmed lines into `_REGIONAL_BASELINES` and delete that block's `UNVERIFIED`
header. Lines you cannot confirm stay out. A missing line makes the brief
quieter; a wrong line makes it wrong, and the model trusts this block completely.

## How these values were gathered, and why that matters

Web search only. The sandbox that drafted this could not open a single one of the
cited URLs, so the citations are search-engine attributions, not pages anyone
read. That is a weaker standard than the house gate, which asks for two
independent *working* sources.

It is not a theoretical worry. A first search for the Treasurer returned "Andrew
Charlton"; a targeted follow-up returned Jim Chalmers, with Charlton as Cabinet
Secretary. One of those is wrong, and only the cross-check caught it. Assume at
least one more error survives in this file and check accordingly.

Confidence column: **High** means two or more independent results agreed and a
targeted cross-check confirmed it. **Medium** means results agreed but nothing
independently confirmed it. **Unconfirmed** means it needs work before use.

---

## Australia: ministry and politics

| Line | Value found | Confidence | Sources |
| --- | --- | --- | --- |
| Prime Minister | Anthony Albanese (ALP) | High | [foreignminister.gov.au](https://www.foreignminister.gov.au/), corroborated across Aug 2026 ministerial activity |
| Deputy PM and Minister for Defence | Richard Marles | High | [Minister for Defence](https://en.wikipedia.org/wiki/Minister_for_Defence_(Australia)) plus Aug 2026 activity |
| Minister for Foreign Affairs | Penny Wong (since May 2022) | High | [foreignminister.gov.au](https://www.foreignminister.gov.au/) — Lowy Institute Q&A, 20 Aug 2026 |
| Minister for Defence Industry and Pacific Island Affairs | Pat Conroy | High | [ministers.dfat.gov.au](https://ministers.dfat.gov.au/minister/pat-conroy) |
| Treasurer | Jim Chalmers (since 23 May 2022) | High | [ministers.treasury.gov.au](https://ministers.treasury.gov.au/ministers/jim-chalmers-2022) — **note the failed first search above** |
| Opposition Leader | Angus Taylor | Medium | [The Conversation](https://theconversation.com/view-from-the-hill-angus-taylor-appoints-tim-wilson-as-part-of-a-new-look-liberal-economic-team-274831) — inferred from him appointing the shadow economic team; confirm directly |
| Shadow Treasurer | Tim Wilson | Medium | [The Conversation](https://theconversation.com/view-from-the-hill-angus-taylor-appoints-tim-wilson-as-part-of-a-new-look-liberal-economic-team-274831) |
| Shadow Foreign Affairs | Ted O'Brien (conflicting) | Unconfirmed | One search returned O'Brien for foreign affairs, another put him at Treasury. Resolve before use. |
| Shadow Defence | not found | Unconfirmed | — |
| Governing party and seat margin | not found | Unconfirmed | Needs the AEC or the House of Representatives party-status page |
| Next federal election window | not found | Unconfirmed | Term runs from the May 2025 election; derive the constitutional window |

`collect.py`'s `AUSPAC_KEYWORDS` still lists `sussan ley|dutton` and no
`angus taylor`. If Taylor confirms as Opposition Leader, add him: the regex is
how opposition stories get picked up at all.

## New Zealand

| Line | Value found | Confidence | Sources |
| --- | --- | --- | --- |
| Prime Minister | Christopher Luxon (National) | High | [beehive.govt.nz](https://www.beehive.govt.nz/release/general-election-be-held-7-november) |
| Minister of Foreign Affairs | Winston Peters (NZ First) | High | [Lowy Interpreter](https://www.lowyinstitute.org/the-interpreter/domestic-politics-new-zealand-s-defence) — attended ANZMIN, Canberra, March 2026 |
| Minister of Defence | Judith Collins (National) | High | Same ANZMIN reporting |
| Coalition composition | National / ACT / NZ First | High | [beehive.govt.nz portfolio index](https://www.beehive.govt.nz/portfolio/nationalactnew-zealand-first-coalition-government-2023-2026/defence) |
| Next general election | **Saturday 7 November 2026**, advance voting from 26 October | High | [beehive.govt.nz](https://www.beehive.govt.nz/release/general-election-be-held-7-november), [elections.nz](https://elections.nz/media-and-news/2026/key-dates-for-2026-general-election) |

**This block has a shelf life of ten weeks.** Every NZ name above is a caretaker
question from 7 November and likely wrong soon after. Diarise a review for the
week of 9 November. Already in the calendar as a confirmed date.

## Defence

| Line | Value found | Confidence | Sources |
| --- | --- | --- | --- |
| 2026 National Defence Strategy | Released; reported 17 April 2026 | Medium | [USNI News](https://news.usni.org/2026/04/17/2026-australian-national-defence-strategy) |
| Defence spending as a share of GDP, and trajectory | not found | Unconfirmed | Take it from the NDS itself or the Budget papers, not from commentary |
| Headline force-structure decisions | not found | Unconfirmed | The NDS is the primary source |
| NZ defence capability plan status | not found | Unconfirmed | Check MoD NZ; note NZ is reported open to an Australia–Fiji defence arrangement |

## Economy

| Line | Value found | Confidence | Sources |
| --- | --- | --- | --- |
| RBA cash rate | **4.35%**, held 11 August 2026, unanimous | High | [RBA media release](https://www.rba.gov.au/media-releases/2026/mr-26-19.html), [SBS](https://www.sbs.com.au/news/live-blog/rba-august-2026-interest-rates-decision-live/trlncz8i2) |
| RBA context | Second consecutive hold after three increases earlier in 2026; trimmed mean still above target; not expected near the 2–3% midpoint until late 2027 | Medium | [RBA](https://www.rba.gov.au/media-releases/2026/mr-26-19.html) |
| RBNZ official cash rate | not found | Unconfirmed | rbnz.govt.nz, and note the next review date |
| Australia's top export markets and China's share | not found | Unconfirmed | DFAT trade statistics |

The cash rate moves roughly every six weeks, so it dates faster than anything
else in the block. Consider citing it with its decision date, as above, so a
stale figure is visible as stale rather than silently wrong.

## Pacific

| Line | Value found | Confidence | Sources |
| --- | --- | --- | --- |
| PIF membership | 18 members | Medium | [forumsec.org](https://forumsec.org/events/pacific-islands-forum-leaders-meeting) — confirm against the Secretariat's own membership page |
| Current chair | Jeremiah Manele, PM of Solomon Islands | High | [forumsec.org](https://forumsec.org/publications/release-forum-troika-leaders-meet-brisbane) |
| Incoming chair | Surangel Whipps Jr, President of Palau, from the Koror meeting | High | Same Troika release |
| 55th Leaders Meeting | **30 August to 4 September 2026, Koror, Palau.** Theme "Building Economies" | High | [PRIF](https://www.theprif.org/event/regional-event/2026-08-30/55th-pacific-islands-forum-leaders-meeting), [IISD SDG Knowledge Hub](https://sdg.iisd.org/events/55th-pacific-islands-forum-leaders-meeting/) |
| Taiwan at the 2026 Forum | Reported attending after a 2025 absence | Medium | [Focus Taiwan](https://focustaiwan.tw/politics/202602240013), [China-Global South Project](https://chinaglobalsouth.com/2026/08/09/taiwan-pacific-islands-forum-palau-china/) |
| Compact of Free Association states | not found | Unconfirmed | Three states; confirm each against the US State Department |
| Which Pacific states recognise Taiwan | not found | Unconfirmed | Changed as recently as 2024; confirm against MOFA and a second source |

The Leaders Meeting is already in `data/calendar.json` as a confirmed entry, so
`calendar_watch` can use the date. The chair handover falls inside that meeting,
which is worth a line in the baselines once confirmed: getting it wrong means
naming the wrong head of government as Forum chair during the one week the brief
will most need to.
