# Per-edition values

Everything in `shared/` is byte-identical across the four briefs. Everything
that legitimately differs between them is here, in one table, so a port is a
matter of reading a row rather than searching four repositories.

| | Korea | Japan | China | Australia |
|---|---|---|---|---|
| Chair | CSIS Korea Chair | CSIS Japan Chair | CSIS China Teams | CSIS Australia Chair |
| Contact | Andy Lim | **Deirdre Martin** | — | — |
| Contact email | alim@csis.org | **dmartin@csis.org** | — | — |
| Accent | `#0052B4` taeguk blue | — | — | teal |
| Subject | `Korea Daily Brief \| Tuesday, September 8, 2026` | same shape | same shape | same shape |

Fill the blanks before porting; do not carry Korea's values across. The China
edition previously shipped for months with "CSIS Korea Chair" in its masthead
and footer, which is the precise failure this table exists to prevent.

## The contact appears in more than one place

Changing it means changing all of them:

- the footer disclaimer in `render.py` ("To report errors or other issues,
  please contact …")
- `DIGEST_REPLY_TO` in `send_email.py`, which is where a reader's reply goes
- the failure-alert address in `.github/workflows/daily-digest.yml`, which is
  who hears about it when a run dies

A brief whose footer names one person and whose Reply-To goes to another is
worse than either alone, because the reader cannot tell which is intended.
