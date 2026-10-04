# Verdant Filings

Regulatory filings for the companies you follow, translated to English with a short overview,
plus company news, a discussion room per company, and Telegram alerts. Starts with Korea (DART);
built so other countries plug in later.

Everything here runs on free tiers.

## What you need (all free)

| Piece | Where | Why |
|---|---|---|
| DART API key | https://opendart.fss.or.kr (sign up, "인증키 신청") | Korean filings. 20,000 calls/day. |
| Postgres database | https://supabase.com or https://neon.tech | Streamlit Cloud wipes local files on restart, so accounts, watchlists and chat need a real database. |
| Telegram bot | Message @BotFather, send /newbot | Alerts. Keep the token secret. |
| Gemini API key (optional) | https://aistudio.google.com/apikey | Better English summaries. Without it, a rule-based summary is used. |
| GitHub repo | github.com | Hosts the code and runs the background checker. |

## Deploy

1. Push this folder to a GitHub repo (private is fine). Don't commit `.streamlit/secrets.toml`.
2. Create the Postgres database and copy its connection string.
   On Supabase use the "Session pooler" URI (Project > Connect), it looks like
   `postgresql://postgres.xxxx:PASSWORD@aws-0-region.pooler.supabase.com:5432/postgres`.
3. On https://share.streamlit.io, create an app from the repo with `app.py` as the entry point.
4. In the app's Settings > Secrets, paste the contents of `.streamlit/secrets.toml.example` and fill in your values.
   Use a long ADMIN_PASSWORD; that account is created on first start.
5. In the GitHub repo, Settings > Secrets and variables > Actions, add the same values as repository secrets:
   `DATABASE_URL`, `DART_API_KEY`, `TELEGRAM_BOT_TOKEN`, and optionally `TELEGRAM_CHANNEL_ID`, `GEMINI_API_KEY`, `APP_URL`.
6. In the Actions tab, enable workflows and run "Poll filings" once by hand to check it works.

## How it works

The website never contacts regulators directly: DART blocks the cloud servers Streamlit runs on.
The GitHub job (`worker.py`) does all regulator work and writes to the database; the website reads it.

- **Today page** checks DART for each company on your watchlist (at most once every 10 minutes per company)
  and lists today's filings with English titles. Common DART titles use a hand-checked glossary; the rest are
  machine translated and cached.
- **Overview and translation** downloads the filing, translates the opening section and writes a 3 to 5 point
  overview. Results are saved, so each filing is processed once.
- **Background worker** (`worker.py`, run by GitHub Actions every 10 minutes during Korean market hours,
  hourly otherwise) syncs every watched company and sends Telegram alerts for new filings, even when nobody has
  the site open. Streamlit's free apps go to sleep, so the alerts can't rely on the site itself.
- **Telegram**: each member connects their own chat on the Account page and gets alerts for their watchlist.
  Set `TELEGRAM_CHANNEL_ID` to also post every filing to one channel.
- **News** comes from Google News RSS: English press, and Korean press with translated headlines.
- **Discussion** is one room per company, refreshing every 20 seconds.

## Security

- Passwords are hashed with bcrypt. After 5 failed sign-ins a username is locked for 15 minutes.
- Sign-ups are invite-only by default (`SIGNUP_MODE`). Set to `open` or `closed` as you like.
- All secrets live in Streamlit/GitHub secrets, never in code.
- Chat messages and all remote text are HTML-escaped before display; posting is rate limited.
- Database access goes through SQLAlchemy with bound parameters.
- Sessions end after 12 hours idle. Refreshing the browser signs you out (a Streamlit limitation;
  a cookie-based "remember me" can be added later).

## Monetizing later

Each user has a `plan` (`free` or `pro`). Plans already limit watchlist size (`FREE_WATCHLIST_LIMIT`,
`PRO_WATCHLIST_LIMIT`), and admins change plans on the Admin page. To charge, add a Stripe Payment Link and a
small webhook that sets `plan = 'pro'`. Other natural pro features: Telegram alerts, full-document translation,
more companies, CSV export.

## Features

- **Today:** a day-in-brief row of category counts, category filters, a by-time or by-company view, and the share
  price move on each filing day.
- **Categories** assigned automatically from titles: Earnings, Dividend, Buyback, Insider trade, Stake change,
  Capital raise, M&A, Contract, Report, Meeting, Board, Legal.
- **Company page:** a price chart with filing dates marked, private notes, and category filters.
- **Watchlist:** letter logos, 30-day sparklines, and alerts per company (All filings, Major only, Off).
- **Search** across all stored filings, starred filings, and CSV export.
- **Ask about a filing** (needs GEMINI_API_KEY or ANTHROPIC_API_KEY).
- **Alerts:** instant or a daily digest at your time; skip insider trades; a weekly report on Mondays; keyword alerts
  matched market-wide for Korea, USA, Poland and Japan (keywords translated into each market's language).
- **Remember me** for 30 days (a random token in a browser cookie; only its hash is stored).
- **Company insight:** an AI brief of recent filings (weekly, needs Gemini), upcoming dates announced in filings
  (meetings, record and payment dates, results), financial figures (Korea: DART key accounts, yearly; USA: SEC
  company facts, yearly and quarterly), and insider trades (USA: Form 4; Korea: DART executive reports).
- **Portfolio:** buys and sells with dates; time-weighted returns for Today, 1M, 3M, 6M, YTD, 1Y, 2Y, 3Y and All
  compared with SPY or QQQ; a performance chart; allocation by company, country and currency; rebalancing to target
  weights (with optional new cash); values in USD or a chosen home currency using daily exchange rates.
- **Price alerts** by Telegram: daily move above a %, or price above/below a level (whole watchlist or one company).
- **Telegram commands:** /list /today /add /remove /portfolio /price /help (answered at the next job run).
- **Housekeeping:** daily clean-up (filings older than 2 years except starred, long texts after 180 days, expired
  sign-ins, old logs) and an Admin health panel with runs, warnings, usage and table sizes.
- **Calendar:** a month view (agenda list on phones) of results releases, meetings and dividend dates announced in
  filings, for your watchlist and holdings.
- **Ask your filings:** a question box on Search that answers from your companies' filings with numbered sources.
- **Broker CSV import** on Portfolio > Transactions, with automatic column matching, row-by-row checks, agorot
  conversion for Israel and duplicate skipping. Transactions can also be edited.
- **Today dashboard:** portfolio value and move, filings today, major filings and dates in the next 7 days.
- **Phone layout:** a bottom menu and denser tables on small screens. Real logos where available (US logo service;
  website icons for Korean and Israeli companies), letter tiles otherwise.
- **Job scheduling:** filings and alerts every run; dates every 30 minutes; insider trades hourly; financials,
  AI briefs and logos every 6 hours; clean-up daily.
- **Dark mode** following the device setting, or chosen in the ⋮ menu, then Settings.

Share prices come from free sources (Yahoo Finance chart data, then Stooq) and may be delayed or unavailable for
some markets.

## Translation

Titles and filing text are translated with Google Translate first. Google often refuses requests from shared
cloud servers such as GitHub's, so the app then uses Gemini (if `GEMINI_API_KEY` is set; free tier at
https://aistudio.google.com/apikey) and finally MyMemory (free, no key, one title at a time; the SEC contact
email raises its daily allowance). Titles that couldn't be translated are retried on later runs.

## Poland (NewConnect)

Reads ESPI and EBI reports of NewConnect companies from the exchange's website (no key). Unofficial: PAP
sells the official feed. Titles are translated from Polish; many reports include the company's own English
version, which is used for overviews. Turn on with `ENABLE_NEWCONNECT = "true"` in GitHub Actions secrets
and Streamlit secrets.

## Australia (ASX)

Reads the data service behind the ASX website: no key, English, with ASX's own "price sensitive" flag.
It is unofficial (ASX sells the official ComNews feed) and could change or be blocked; ASX's website terms
limit commercial reuse. Turn it on with `ENABLE_ASX = "true"` in GitHub Actions secrets and Streamlit
secrets; set it to "false" to switch Australia off. Telegram alerts go out only for price-sensitive
announcements unless `ASX_ALERTS = "all"`. Overviews are read from the announcement PDFs.

## USA (SEC EDGAR)

Free, no key. Add `SEC_CONTACT_EMAIL` (a real address; the SEC requires it in every request) to GitHub
Actions secrets and to Streamlit secrets, then run Poll filings once to load about 10,000 tickers.
Covers NYSE, Nasdaq, SEC-reporting OTC companies, and foreign issuers listed in the US (6-K, 20-F, 40-F).
8-K titles show what the report is about (earnings, officer change, material agreement...).

## Japan (EDINET)

Free key: https://disclosure2.edinet-fsa.go.jp, create an account, then issue an API key (API キー発行).
Add `EDINET_API_KEY` to GitHub Actions secrets and to Streamlit secrets, then run Poll filings once to load
the company list. Tickers are 4-character TSE codes (7203 Toyota, 6758 Sony).

EDINET covers statutory filings (annual and semi-annual reports, extraordinary reports, large shareholding
reports, tender offers, buyback reports). Exchange announcements on TDnet (earnings flashes, guidance) have
no free API and aren't included. EDINET data is under the Public Data License 1.0: commercial use is
allowed with the credit line the app shows next to Japanese filings.

## Portfolio, journal and briefings

- **Portfolio tabs:** Overview (returns, chart, heatmap, allocation), Holdings, Cash (deposits, withdrawals, income,
  fees; value then includes cash and returns use money in and out), Rebalance, Transactions (with broker CSV
  import), Goals (projection, monthly saving and return needed), Price alerts, Report & share (monthly PDF and a
  read-only link showing percentages only).
- **Journal:** dated entries per company (thesis, buy, sell, update, review, lesson) with conviction, tags and review
  reminders by Telegram or email.
- **Fair value notes** per company with an alert when the price comes within your chosen %.
- **Briefings:** morning brief, weekly AI briefing, monthly PDF report; **email delivery** through SMTP secrets.
- **Signals:** insider buying (2+ insiders within 30 days, or a US purchase of $1M+) and new listings by market.
- **Importance ranking** of filings, **"What changed"** and **highlights** notes on reports (AI), and estimated
  **results dates** on the Calendar from each company's reporting rhythm.
- **Backup:** download all your data as JSON and CSV.
- **Remember me** uses browser local storage as well as a cookie, so it works on Streamlit Community Cloud.

## More markets

Each has an on/off secret (GitHub Actions and Streamlit): ENABLE_TDNET (Japan timely disclosures, no key),
ENABLE_HKEX (Hong Kong, HKEXnews), ENABLE_OSLO (Norway, NewsWeb), ENABLE_TWSE (Taiwan, official open data; the feed
shows the current day, so history builds from when it's on), ENABLE_AMF (France, official AMF open data; companies by
ISIN), ENABLE_UK_NSM (UK, FCA National Storage Mechanism; companies by London ticker, LEI from GLEIF) and
ENABLE_NORDIC (Sweden, Denmark, Finland via Nasdaq Nordic; companies by ticker). Website-based sources pause
themselves for some hours if a site refuses a request. Titles are translated where needed.

## Israel (MAYA website, personal use)

With `ENABLE_MAYA = "true"` (GitHub Actions secrets and Streamlit secrets), Israeli company reports are read from
the public MAYA website: the company list once a day and one request per watched Israeli company per run. English
report subjects come from MAYA. It is unofficial and for personal use: Israel is visible to admin accounts only and
never posted to the public Telegram channel. If MAYA ever refuses a request, Israel pauses for 24 hours (shown as a
warning on the Admin health panel) and does not retry. Adding TASE_API_KEY switches to the official paid feed below.

## Israel (TASE MAYA)

1. Create an account at https://datahub.tase.co.il, subscribe to "Market Announcements feed (MAYA)"
   (free trial: 100 requests / 4 weeks) and copy your API key.
2. In the TASE developer portal, find the "reports by date" interface of that product and copy its path,
   replacing the date parts with placeholders, e.g. `group/reports-by-date/{yyyy}/{m}/{d}`.
3. GitHub Actions secrets: `TASE_API_KEY`, `TASE_MAYA_PATH`, and optionally `TASE_DAILY_CALL_LIMIT`
   (default 3 a day, spread across the day, to fit the trial). Streamlit secrets: `TASE_API_KEY`.
4. Run the "TASE probe" workflow once to check the connection and the response format.

Tickers are TASE symbols (TEVA, LUMI, POLI). The company list comes from TASE's free endpoints.
On a paid plan, raise TASE_DAILY_CALL_LIMIT (for example to 300) so every run checks MAYA.

## Adding a country

Write `sources/<cc>_<name>.py` with a class that subclasses `FilingSource` (see `sources/base.py` and
`sources/kr_dart.py`), then `register()` it in `sources/__init__.py`. Everything else (watchlists, alerts,
translation, news, chat) works per market automatically. Good next sources: SEC EDGAR (US, free, no key),
EDINET (Japan, free key), HKEXnews (Hong Kong).

## Run locally

```
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # fill in DART_API_KEY and admin details
streamlit run app.py
```
Without `DATABASE_URL` it uses a local SQLite file.

## Limits to know

- The free Google translator used by `deep-translator` is unofficial and can rate-limit heavy use.
  Translations are cached, and a Gemini key gives better summaries directly from the Korean text.
- DART document text isn't available for some exchange-only notices; those show a link to the original.
- GitHub pauses scheduled workflows in repos with no commits for 60 days; re-enable in the Actions tab.
