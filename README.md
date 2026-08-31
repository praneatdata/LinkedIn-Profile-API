# LinkedIn Profile API

A small HTTPS API that turns a LinkedIn profile URL into clean, structured JSON.

```
GET /profile?url=https://www.linkedin.com/in/some-slug/
```

Requests go **directly to LinkedIn's internal Voyager JSON API over HTTP**. There is no
browser anywhere in the stack — no Selenium, no Playwright, no Puppeteer, no headless
Chrome. What makes a plain HTTP client work against Voyager is TLS fingerprint
impersonation (`curl_cffi`), browser-consistent headers, and a residential egress proxy.

---

## Contents

- [What you get](#what-you-get)
- [Quick start](#quick-start)
- [Getting the LinkedIn cookies](#getting-the-linkedin-cookies)
- [Getting a proxy](#getting-a-proxy-required)
- [API reference](#api-reference)
- [How it works](#how-it-works)
- [Testing](#testing)
- [Deployment](#deployment)
- [Known limitations](#known-limitations)
- [Legal and ethical notes](#legal-and-ethical-notes)

---

## What you get

| Field | Notes |
|---|---|
| `full_name`, `first_name`, `last_name` | |
| `headline` | falls back to the mini-profile `occupation` |
| `location` | `{city, country, full}` — `city` only when LinkedIn gives a comma-delimited string |
| `about` | the About / summary section |
| `experience[]` | title, company, company URL, location, `YYYY-MM` dates, `is_current`, description |
| `education[]` | school, degree, field of study, start/end year |
| `skills[]` | de-duplicated, in LinkedIn's own order |
| `certifications[]` | name, authority, credential URL, dates |
| `languages[]` | name + human-readable proficiency |
| `profile_picture_url`, `background_image_url` | highest-resolution artifact available |
| `public_identifier`, `linkedin_url`, `urn` | |
| `warnings[]` | why anything is missing — see [graceful degradation](#graceful-degradation) |

---

## Quick start

```bash
git clone <your-fork-url> linkedin-profile-api
cd linkedin-profile-api

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt        # runtime + test deps

cp .env.example .env                       # then fill it in — see the two sections below
```

Run it:

```bash
uvicorn app.main:app --reload
```

- <http://localhost:8000/docs> — interactive Swagger UI
- <http://localhost:8000/health> — `{"status":"ok"}`
- <http://localhost:8000/readiness> — reports whether cookies/proxy are configured (booleans only, never values)

The offline test suite needs no credentials at all:

```bash
pytest -q          # 186 tests, zero network requests
```

Python 3.11+ is required. Runtime dependencies are in `requirements.txt`;
`requirements-dev.txt` adds the test tooling.

---

## Getting the LinkedIn cookies

> **Use a throwaway LinkedIn account.** The account doing the fetching can be
> rate-limited or restricted. Do not use a personal or work account.

There is deliberately **no programmatic login** — automated logins reliably trip
LinkedIn's `CHALLENGE` flow and can lock the account. Capture the cookies by hand:

1. Log into LinkedIn in a browser as the throwaway account.
2. Open DevTools → **Application** → **Storage** → **Cookies** → `https://www.linkedin.com`.
3. Copy two values into `.env`:

| Cookie | Env var | Notes |
|---|---|---|
| `li_at` | `LI_AT` | the session cookie |
| `JSESSIONID` | `LI_JSESSIONID` | copy it **including the double quotes**, e.g. `"ajax:1234567890"` |

```dotenv
LI_AT=AQEDAT...
LI_JSESSIONID="ajax:1234567890"
```

LinkedIn requires the `csrf-token` request header to equal the `JSESSIONID` value
*without* its quotes, while the cookie itself keeps them. The app derives both from the
one variable, and re-adds the quotes if you paste the value unquoted.

Cookies expire. When they do, `/profile` answers **502** with an explicit
"refresh the li_at and JSESSIONID cookies" message.

---

## Getting a proxy (required)

LinkedIn blocks datacenter IP ranges — including every IP your hosting platform will
give you — with `HTTP 999`. A **residential or mobile** proxy is not optional.

```dotenv
PROXY_URL=socks5h://user:pass@host:port
```

Use `socks5h://` rather than `socks5://` so DNS is resolved proxy-side; `http://`
proxies work too. Any commercial residential provider is fine.

**The proxy check is fail-closed.** With `PROXY_URL` unset, `/profile` returns 500 and
sends nothing — it will never fall back to the host's own IP address, because doing so
would leak the server IP and burn the LinkedIn account on the first request.

---

## API reference

### `GET /profile`

| Param | In | Required | Description |
|---|---|---|---|
| `url` | query | yes | A LinkedIn member profile URL containing `/in/` |
| `X-API-KEY` | header | only if the server sets `API_KEY` | your API key |

Accepted `url` forms — all resolve to the same profile and the same cache entry:

```
https://www.linkedin.com/in/some-slug/
https://linkedin.com/in/some-slug?utm_source=x
https://in.linkedin.com/in/some-slug
https://www.linkedin.com/in/some-slug/details/experience/
https://www.linkedin.com/mwlite/in/some-slug
www.linkedin.com/in/some-slug
```

Company and school URLs are rejected with 422.

**Request**

```bash
curl -s -H "X-API-KEY: $API_KEY" \
  "http://localhost:8000/profile?url=https://www.linkedin.com/in/some-slug/" \
  | python -m json.tool
```

**Response** `200 OK` (this is the synthetic test fixture, so the shape is exact):

```json
{
  "public_identifier": "jane-doe-example",
  "linkedin_url": "https://www.linkedin.com/in/jane-doe-example",
  "urn": "urn:li:fs_profile:ACoAAAExample01",
  "full_name": "Jane Doe",
  "first_name": "Jane",
  "last_name": "Doe",
  "headline": "Senior Software Engineer at Acme Corp | Distributed Systems & Developer Tooling",
  "location": {
    "city": "San Francisco",
    "country": "United States",
    "full": "San Francisco, California"
  },
  "about": "Backend engineer with 9 years building high-throughput distributed systems. ...",
  "profile_picture_url": "https://media.licdn.com/dms/image/v2/.../profile-displayphoto-shrink_800_800/...",
  "background_image_url": "https://media.licdn.com/dms/image/v2/.../profile-displaybackgroundimage-shrink_1400_425/...",
  "experience": [
    {
      "title": "Senior Software Engineer",
      "company": "Acme Corp",
      "company_url": "https://www.linkedin.com/company/acme-corp-example",
      "location": "San Francisco, California, United States",
      "start_date": "2021-03",
      "end_date": null,
      "is_current": true,
      "description": "Tech lead for the event ingestion platform. ..."
    },
    {
      "title": "Software Engineer II",
      "company": "Globex Corporation",
      "company_url": "https://www.linkedin.com/company/globex-corporation-example",
      "location": "Seattle, Washington, United States",
      "start_date": "2018-08",
      "end_date": "2021-02",
      "is_current": false,
      "description": "Built the internal feature-flag service used by 40+ teams. ..."
    }
  ],
  "education": [
    {
      "school": "Stanford University",
      "degree": "Master of Science - MS",
      "field_of_study": "Computer Science",
      "start_year": 2014,
      "end_year": 2016
    }
  ],
  "skills": ["Distributed Systems", "Python", "Apache Kafka", "Kubernetes", "Rust"],
  "certifications": [
    {
      "name": "AWS Certified Solutions Architect - Associate",
      "authority": "Amazon Web Services (AWS)",
      "url": "https://www.credly.com/badges/00000000-0000-0000-0000-000000000000",
      "start_date": "2022-06",
      "end_date": "2025-06"
    }
  ],
  "languages": [
    { "name": "English", "proficiency": "Native or bilingual proficiency" },
    { "name": "Spanish", "proficiency": "Professional working proficiency" }
  ],
  "warnings": []
}
```

Every key is always present. Unavailable scalars are `null`, unavailable lists are `[]`,
and dates are `"YYYY-MM"` (or `"YYYY"` when LinkedIn only gives a year).

#### Error codes

| Status | Meaning | What to do |
|---|---|---|
| `401` | Missing or invalid `X-API-KEY` | send the right key |
| `404` | Profile does not exist, was renamed, or is not visible to the session | check the slug |
| `422` | `url` is absent, malformed, or not a LinkedIn `/in/` URL | fix the URL |
| `429` | LinkedIn is rate-limiting the session | back off; the account may be restricted |
| `500` | Server misconfigured — `PROXY_URL` or the cookies are unset | set the env vars |
| `502` | Session cookies expired/rejected, or an unexpected upstream response | refresh the cookies |
| `504` | Upstream request to LinkedIn timed out | retry later |

Errors are `{"detail": "..."}`. Messages are fixed strings chosen for the caller —
no cookie, proxy credential, or upstream exception text can appear in one.

### `GET /health`

`{"status":"ok"}`. No auth. Use as the platform health check.

### `GET /readiness`

Reports configuration **as booleans**, plus cache statistics. Useful for confirming a
deploy picked up its environment without exposing any of it:

```json
{
  "status": "ok",
  "version": "1.0.0",
  "credentials_configured": true,
  "proxy_configured": true,
  "api_key_enforced": true,
  "impersonate": "chrome",
  "dash_fallback_enabled": false,
  "ready_for_live_requests": true,
  "cache": { "entries": 3, "max_entries": 1024, "ttl_seconds": 86400, "hits": 7, "misses": 3 }
}
```

### Authentication

Set `API_KEY` and every `/profile` call must carry `X-API-KEY`. Leave it unset and auth
is disabled — convenient locally, but **always set it on a deployed instance**, since
an open instance is an open proxy onto your LinkedIn account. Comparison is
constant-time. `/health`, `/readiness` and `/docs` stay open so the platform can probe them.

---

## How it works

```
GET /profile?url=...
      │
      ├─ extract_public_id()      URL → slug, or 422 (no upstream request wasted)
      ├─ require_api_key()        X-API-KEY, when configured
      ├─ cache.get(slug)          24h TTL → hit returns here, LinkedIn never touched
      │
      ├─ LinkedInClient.fetch_raw(slug)
      │     ├─ fail closed unless PROXY_URL is set
      │     ├─ GET voyager/api/identity/profiles/{slug}/profileView
      │     │     via curl_cffi (impersonate=chrome) through the residential proxy
      │     │     headers: csrf-token = JSESSIONID minus quotes, restli 2.0.0, …
      │     ├─ status → typed exception (401/403, 404/410, 429/999, 5xx, HTML authwall)
      │     └─ retry on rate-limit/timeout only, exponential backoff + jitter
      │
      ├─ parse_profile(raw, slug)  flatten included[] → Profile   (never raises)
      └─ cache.set(slug, profile)
```

### Why Voyager directly

Voyager is the JSON API LinkedIn's own web app calls. Hitting it directly is far
cheaper and more reliable than rendering and scraping HTML, and it is the only approach
compatible with the no-browser constraint. `identity/profiles/{slug}/profileView` is the
primary endpoint because its URL is stable and carries no `decorationId` to drift.

### Why `curl_cffi`

A stock `requests`/`httpx` TLS handshake has a JA3 fingerprint nothing like Chrome's,
and LinkedIn challenges it immediately regardless of how perfect the headers are.
`curl_cffi` performs the handshake as Chrome does. The `IMPERSONATE` env var selects the
target (default `chrome`); bump it if challenges start appearing.

### The parser

Voyager's normalized responses are a flat `included[]` array where the profile, each
position, each school and each skill are all siblings, cross-referenced by `entityUrn`.
Nothing about that array's order or length is contractual, so `app/linkedin/parser.py`:

- classifies each entity by its **URN namespace** — the third colon-segment of
  `urn:li:<ns>:<id>` — falling back to the `$type` leaf. Matching the namespace
  *exactly* is what stops `urn:li:fsd_profilePosition:(…)` being read as a profile,
  which naive substring matching on `fsd_profile` gets wrong;
- **never indexes into the array.** A test shuffles `included[]` under four seeds and
  asserts byte-identical output;
- restores LinkedIn's own ordering from the `*elements` URN lists when present;
- handles all three payload shapes seen in the wild: normalized `profileView`
  (`fs_*` URNs), legacy non-normalized `profileView` (inline `*View.elements`), and
  modern `dash/profiles` (`fsd_*` URNs, `dateRange{start,end}` instead of
  `timePeriod{startDate,endDate}`);
- picks the highest-resolution image artifact rather than assuming a position in the list.

### Graceful degradation

`parse_profile` **never raises.** LinkedIn gates different sections on different
profiles, so a section that cannot be read produces an entry in `warnings[]` and an
empty value:

```json
{
  "full_name": "Min Imal",
  "experience": [],
  "warnings": [
    "about/summary section is empty or not visible",
    "experience section is empty or not visible on this profile"
  ]
}
```

A half-readable profile is more useful to a caller than a 500, and `warnings[]` says
exactly what was missing rather than leaving you to guess.

### Caching

A 24h in-process TTL cache (`CACHE_TTL_SECONDS`) keyed by public identifier — so all
the URL spellings above share one entry. This is the main defence for the LinkedIn
account: a cache hit is a request that never happens. Failures are deliberately **not**
cached, so a transient 429 does not poison a slug for a day. Deployments run a single
replica on purpose; more replicas would mean more cache misses.

### Secret handling

- `.env` is gitignored; `.env.example` lists key names only.
- Typed exceptions carry fixed, caller-safe messages. libcurl embeds the full proxy URL
  — password included — in its error strings, so upstream messages are never reused and
  the exception cause is severed (`raise … from None`) to keep it out of tracebacks.
- A logging filter (`app/redaction.py`) scrubs the proxy URL, its userinfo, the
  cookies and the API key out of log messages *and* formatted tracebacks, so
  debuggability survives without leaking.

### Layout

```
app/
  main.py              FastAPI app, routes, exception → HTTP mapping
  config.py            pydantic-settings; every field optional so it boots bare
  models.py            the response contract
  security.py          X-API-KEY dependency
  cache.py             thread-safe TTL cache
  redaction.py         secret-scrubbing log filter
  linkedin/
    client.py          Voyager HTTP client + URL → slug extraction
    endpoints.py       endpoint paths
    parser.py          included[] → Profile
    exceptions.py      typed failures carrying status + safe message
scripts/
  capture_fixture.py   one-off: fetch one profile, save raw JSON as a fixture
tests/                 186 offline tests + 1 gated live test
```

---

## Testing

```bash
pytest -q                    # 186 tests, no network, no credentials needed
pytest tests/test_parser.py -q
```

The offline suite makes **zero** LinkedIn requests. Two autouse fixtures enforce that:
one hides any local `.env` and unsets every credential so results are identical to CI;
the other replaces the client's single transport seam with a tripwire, so a test that
accidentally tries to reach the network fails loudly instead of firing a request.

Coverage worth calling out:

| Area | What's asserted |
|---|---|
| Parser | all three payload shapes; order-independence under shuffling; highest-res image; URN-namespace confusion; ~15 hostile/malformed payloads that must not raise |
| Client | csrf-token derivation (quoted and unquoted); fail-closed proxy *before* any request is built; 15 URL forms and 13 rejected ones; every status mapping; retry on 429/timeout but not on 401/404; dash fallback; **that proxy credentials never appear in a raised error** |
| API | every error code; all schema keys present; cache prevents a second upstream call; failures aren't cached; unexpected exceptions become a generic 500 |
| Security | wrong/missing/blank keys; unauthorized requests never reach LinkedIn; the key is never echoed back |
| Redaction | messages, interpolated args, and tracebacks are all scrubbed |

### Live smoke test

One gated test, one request:

```bash
RUN_LIVE=1 pytest -m live -q -s
RUN_LIVE=1 LIVE_TEST_SLUG=some-public-slug pytest -m live -q -s
```

It skips with an explicit message if `RUN_LIVE` is unset or the credentials/proxy are
missing. It deliberately does not loop or parameterize over profiles.

### Capturing a real fixture

```bash
python scripts/capture_fixture.py --slug some-public-slug
python scripts/capture_fixture.py --url https://www.linkedin.com/in/some-slug/
```

One request per invocation. Output defaults to `tests/fixtures/live_profile.json`,
which is **gitignored** — a real capture contains a real person's data. The committed
`tests/fixtures/sample_profile.json` is synthetic and is what drives the offline suite.

Without credentials the script prints `⏸ BLOCKED: needs … from human` and explains how
to capture the payload by hand (DevTools → Network → filter `profileView` → copy the
JSON response).

---

## Deployment

Any container host works. `Dockerfile` runs as a non-root user, installs runtime deps
only, and binds `$PORT`.

```bash
docker build -t linkedin-profile-api .
docker run --rm -p 8000:8000 --env-file .env linkedin-profile-api
```

**Railway** (`railway.json` included): create a project from the repo, set the env vars
below in the dashboard, deploy. HTTPS and the certificate are automatic.

**Render** (`render.yaml` included): every secret is declared `sync: false`, i.e. it must
be set in the dashboard and is never stored in the repo.

### Environment variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `LI_AT` | for live calls | — | LinkedIn session cookie (throwaway account) |
| `LI_JSESSIONID` | for live calls | — | CSRF cookie, quotes included |
| `PROXY_URL` | **yes** | — | residential/mobile proxy; fail-closed if unset |
| `API_KEY` | strongly advised | — | required in `X-API-KEY`; unset disables auth |
| `CACHE_TTL_SECONDS` | no | `86400` | profile cache TTL |
| `IMPERSONATE` | no | `chrome` | `curl_cffi` TLS impersonation target |
| `REQUEST_TIMEOUT_SECONDS` | no | `20` | per-request timeout |
| `MAX_RETRIES` | no | `3` | attempts on rate-limit/timeout |
| `PROFILE_VIEW_ENABLED` | no | `true` | use the primary `profileView` endpoint |
| `DASH_DECORATION_ID` | no | — | set to also enable the `dash/profiles` fallback |

Set every secret in the platform's secret store. Never commit them.

> **All LinkedIn traffic must egress through `PROXY_URL`.** Your platform's own
> datacenter IP will be blocked with `HTTP 999`. The fail-closed check enforces this,
> so a deploy that forgets the proxy returns 500 rather than quietly burning the account.

### Verifying a deployment

```bash
curl -s https://<your-app>/health
curl -s https://<your-app>/readiness | python -m json.tool     # expect ready_for_live_requests: true
curl -s -H "X-API-KEY: $API_KEY" \
  "https://<your-app>/profile?url=https://www.linkedin.com/in/<slug>/" | python -m json.tool
```

---

## Known limitations

Being straight about these, because they are properties of the approach rather than
bugs to be fixed:

**It depends on an undocumented internal API.** Voyager is LinkedIn's private
web-app backend. Payload shapes, `decorationId` values and endpoint paths change
without notice. The parser is written to survive shape drift — namespace matching,
`.get()` everywhere, `warnings[]` instead of exceptions — but a large enough change
will still reduce what it can extract. The parser's tests are the early-warning system.

**It needs a valid cookie from a throwaway account, which will eventually be
restricted.** There is no service account and no official credential. Cookies expire
(days to weeks) and must be recaptured by hand; the API surfaces this as a 502 with an
explicit message. Aggressive use gets the account rate-limited or permanently
restricted. This is not viable for production or commercial use.

**It needs a residential/mobile proxy.** Datacenter IPs — every cloud platform's
default — get `HTTP 999`. That is an ongoing cost, and proxy quality directly determines
success rate.

**It is rate-limited by design, and not for bulk extraction.** One profile per request,
a 24h cache, and a single replica. Keep sustained live traffic to roughly one request
per minute with jitter. There is no built-in rate limit on your own endpoint yet, so an
open deployed instance without `API_KEY` is a way to get the account banned by a
stranger — set the key.

**Some fields are gated or simply absent on many profiles.** Contact info and email are
not exposed at all. Skills are frequently truncated to a subset. Certifications,
languages, publications and projects are often empty even when visible in a browser,
because they depend on the viewer's relationship to the member and on the
authenticating session's own privileges. `warnings[]` distinguishes "this profile has
none" from "this section was not visible to us" as far as the payload allows — but the
two are not always distinguishable.

**`location.country` may be an ISO-3166 alpha-2 code.** When LinkedIn exposes only
`countryCode` and not a country name, that code is returned (e.g. `"DE"`) rather than
guessed at. `location.city` is left `null` for metro-area strings like
"San Francisco Bay Area", which are not cities.

**The cache is in-process.** A restart empties it and a second replica would not share
it. That is a deliberate simplification, and the reason deployments run one replica.

**Not verified in this build:** the Docker image has not been built (no Docker daemon
available in the dev environment) and no live LinkedIn request has been made, because
cookies and a proxy were never supplied. Everything else in this README is verified by
the test suite or by running the server.

---

## Legal and ethical notes

This project accesses LinkedIn in a way that **violates LinkedIn's User Agreement**,
which prohibits scraping and use of non-public APIs. It was built as a time-boxed
technical challenge, and it is not suitable for production or commercial use.

The relevant history is worth knowing: *hiQ Labs v. LinkedIn* established that scraping
public data is not a CFAA violation in the US, but it did **not** make it a contract
breach-free activity — LinkedIn prevailed on its breach-of-contract claims, and hiQ
ultimately shut down. Proxycurl, a well-funded commercial LinkedIn data API, shut down
in 2025 under legal pressure. Assume that anything built this way has a short life and
real legal exposure.

It also handles personal data. Under GDPR/CCPA the deployer is the data controller and
owns the lawful basis, the retention decision and any subject-access obligations. The
24h cache means personal data is retained in process memory; there is no persistence
layer, and restarting clears it.

Use a throwaway account, do not extract at scale, and do not deploy this against real
people's data without a basis for doing so.
