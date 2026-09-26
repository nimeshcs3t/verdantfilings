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
