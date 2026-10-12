# WorldPulse AI

AI-assisted news reader that ingests from configured news APIs and permitted RSS feeds,
keeps every story linked to its original publisher, and produces morning/evening briefings.

## Status (honest summary)

Working and tested: ingestion pipeline (RSS live-tested; GNews/NewsAPI adapters tested with mocks only),
deduplication, same-event clustering, extractive fallback summaries, LLM summary validation (mocked),
briefing build, email delivery code (tested with mocks), admin API with token auth, rate limiting,
SSRF-guarded feed fetching, and a responsive web UI.

Also working: local accounts (scrypt passwords, server-side sessions, HttpOnly cookies),
saved stories, reading history, saved topics and publishers, preferences, notification settings,
and a personalized "My Feed". Browser flows were tested end to end (register, save, read history,
preferences, sign out, auth gate).

Not yet built: see "Not yet built" below.

## Run locally

    python -m venv .venv && .venv\Scripts\activate     # Windows (bash: source .venv/Scripts/activate)
    pip install -r requirements-dev.txt
    cp .env.example .env                                 # then fill in values you have
    uvicorn app.main:app --reload                        # http://localhost:8000

Run tests (no external calls, all HTTP is mocked; includes a migration test):

    python -m pytest -q

## Scheduling

Run from OS cron, Windows Task Scheduler, or a managed scheduler (do not use an in-process timer):

    python -m app.scheduler collect            # every 30-60 min; add a more frequent job for breaking news
    python -m app.scheduler briefing morning   # 07:00 Asia/Kolkata
    python -m app.scheduler briefing evening   # 19:00 Asia/Kolkata
    python -m app.scheduler purge              # daily retention cleanup

Each edition is sent once per recipient per calendar day (enforced by a unique constraint).

## Credentials: where to get them

| Variable | Source | Notes |
|---|---|---|
| GNEWS_API_KEY | https://gnews.io dashboard | Check plan limits and terms before production use |
| NEWSAPI_API_KEY | https://newsapi.org | Developer plan is not for production |
| LLM_API_KEY | Your LLM provider's dashboard | Any OpenAI-compatible endpoint |
| SMTP_* | Your email provider | Use an app password, never your main password |
| ADMIN_TOKEN | You generate it | Required for /api/admin/* |

No credentials are shipped. Nothing secret is sent to the browser.

## Publishers and newspapers

`app/registry/publishers.json` is the publisher registry. Add a publisher by adding an entry; no code change.
- `rss_url` is set only for feeds I could confirm are public. Other Indian publishers are `null` until their
  feed URLs and terms are verified on their own sites.
- `epaper_url` is `null` for every publisher. No e-paper link has been verified, so none is shown.
  The UI shows the official website instead.
- Full newspaper PDFs are never downloaded, copied, or embedded.

## API

Interactive docs at `/api/docs` when running. Main endpoints:

- `GET /api/health`, `GET /api/status`
- `GET /api/news?category=&language=&country=&publisher=&q=&breaking=&since_hours=&sort=&page=&page_size=`
- `GET /api/news/{id}`, `GET /api/top`, `GET /api/breaking`, `GET /api/trending`
- `GET /api/events/{id}/coverage`, `GET /api/briefing/{morning|evening}`
- `GET /api/publishers`, `GET /api/newspapers`, `GET /api/publishers/compare`
- Accounts: `POST /api/auth/register`, `POST /api/auth/login`, `POST /api/auth/logout`
- Signed in (session cookie): `GET|PUT /api/me`, `GET|PUT /api/me/preferences`,
  `GET|PUT /api/me/notifications`, `GET /api/me/feed`, `GET /api/me/saved`,
  `PUT|DELETE /api/me/saved/{id}`, `POST /api/me/history/{id}`, `GET /api/me/history`,
  `GET|POST /api/me/follows`, `DELETE /api/me/follows/{id}`
- Admin (header `X-Admin-Token`): `GET /api/admin/dashboard`, `POST /api/admin/collect`,
  `POST /api/admin/publishers/{slug}/enabled?enabled=`, `POST /api/admin/purge`

## Security notes

- API keys are read from the environment only and redacted from error messages.
- Feed URLs are checked against an SSRF guard (public IPs, http/https, standard ports, no credentials).
  DNS rebinding between check and fetch is a residual risk; run behind an egress proxy in production.
- Article text is untrusted. It is quoted into the LLM prompt with an injection guard, and output is validated.
- The frontend builds the DOM with `textContent` only; no `innerHTML` with server data.
- CSP, `X-Frame-Options: DENY`, and `nosniff` are set on responses.
- Admin compares the token in constant time. The admin token is never returned.
- Passwords: scrypt, minimum 10 characters with a letter and a digit. Login errors are generic and take
  similar time for unknown emails.
- Session cookies are HttpOnly and SameSite=Lax (Secure over HTTPS). Only a SHA-256 of the token is stored.
- Mutating API routes take JSON; CORS only allows APP_BASE_URL with credentials.
- Rate limiting is in-process per IP. Use an edge or Redis limiter for multiple instances.

## Known limits

- Summaries without an LLM key are excerpts of the publisher's own description and are flagged "Review".
- Category labels from keyword heuristics are approximate. Provider-supplied categories are preferred.
- Event clustering uses title-word overlap. It can miss matches or merge unrelated stories.
- "Importance" is a recency and coverage score. It does not indicate truth.

## Not yet built

- Password reset and email verification (accounts use local auth; no Supabase auth yet).
- Per-user briefing emails. The scheduler sends the shared briefing to NOTIFY_RECIPIENTS only;
  it does not yet honor each user's notification settings.
- Hindi/English reader toggle for all stories. Hindi appears only when the LLM supplies it.
- Next.js frontend. The current UI is a single static page with no build step.
- Row-level security policies (only relevant if you move to Supabase/Postgres RLS).
- Hindi/English editorial briefings in email beyond the English text.
- Newspaper e-paper integration (none verified).
- Live GNews/NewsAPI runs (credentials not available in this environment).

## Deployment

Run `alembic upgrade head` as a deploy step before starting the app.
Container: `docker build -t worldpulse . && docker run --env-file .env -p 8000:8000 worldpulse`.
Use PostgreSQL via `DATABASE_URL`, run the collection and briefing jobs from your scheduler, and set `APP_BASE_URL` to the public URL.
