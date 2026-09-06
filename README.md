# SpicyTool 🔥

Free, login-free award-flight search. No accounts, no API keys required from
end users, no paywall, no tracking. Returns real, useful results out of the
box, while exposing a provider-agnostic aggregation layer that authorized
commercial feeds can drop into unchanged.

**Stack:** Python 3.12 · FastAPI · AsyncIO · HTTPX · Pydantic v2 · sse-starlette · Redis

> **Live data only.** Searches return results exclusively from the live,
> credentialed providers (Flybasis, AwardTool, PointsYeah, PointsPath). When no
> provider credential is configured, the UI shows an honest "no live provider
> connected" state — it never shows sample itineraries. The first-party
> *modeled* engine (`SpicyToolEngine` + the `/api/v1/search*` routes) is
> **off by default** and only comes back with `SPICYTOOL_MODELED_ENGINE=1`
> (demos / offline tests). Always confirm on the airline's own site before booking.

---

## Quickstart

### Docker (recommended)

```bash
cp .env.example .env
docker compose up --build
# -> http://localhost:8000
```

### No Docker

```bash
./run.sh          # venv + deps + uvicorn on 0.0.0.0:8000
```

### Tests

```bash
python3 -m unittest discover -s backend/tests -p 'test_*.py' -v  # session/HAR/live-verifier regressions
(cd backend && python3 tests_integration.py)                    # offline integration suite
python3 backend/tools/verify_flybasis_socket.py                 # local HTTP/WebSocket contract
```

### Vercel

The repo deploys to Vercel with zero extra setup: the root `app.py`
re-exports the FastAPI app from `backend/main.py`, the root
`requirements.txt` mirrors `backend/requirements.txt`, and `vercel.json`
sets the FastAPI preset (60 s function timeout, mockup/screenshot files
excluded from the bundle). Import the repo in Vercel and deploy — `/`,
`/api/v1/*` and `/api/v2/*` are all served by one function.

Recommended environment variables (Project → Settings → Environment
Variables):

- `AUTH_SECRET` — optional long random string. Without it the signing key is
  derived deterministically from the login config, so sessions stay valid
  across serverless cold starts (set it for a stronger, deployment-agnostic
  key).
- `LOGIN_PIN`, `ALLOWED_LOGIN_EMAILS` — optional overrides.
- `REDIS_URL` — optional; without it the cache runs in memory per instance.

For real award results, configure an official Flybasis token or an authorized
account session in Vercel Environment Variables and redeploy. See
[Flybasis setup and private HAR import](FLYBASIS_GO_LIVE.md). Publicly shared
session tokens must be revoked and replaced; they are never bundled into the app.

---

## Frontend — 1:1 mockup implementation

The UI is a pixel-faithful implementation of the repository's Stitch mockups
(no invented design tokens):

- **Search screen** follows `code 7.html` / `code 12.html` ("Find your
  flight"): `#0D0E10` canvas, `#141416` search card, 52px inputs with
  `#2C2E35` IATA chips, round red Search CTA, calendar popover, promo card.
  v1.0 wiring: the calendar's month navigation no longer closes the popover
  (the old toggle-on-bubble bug), the Passengers control is a three-row
  stepper panel (Adults/Children/Infants), and a Flexibility control
  (±0–3 days) fans out real searches per nearby date.
- **Results screen** follows `code 8.html` ("Choose your flights"):
  `#0D0E11` canvas, sticky header, 3-step stepper, filter-chip toolbar,
  Best/Fastest/Cheapest sort tabs, flight cards with the `w-24 h-11` fare
  tile, "$X Off Retail" savings badge and the dashed timeline expansion.
  Clicking a fare (price) tile **opens the itinerary automatically in its
  own new tab** — there is no button involved (the old **Get VI\*** CTA was
  removed at the owner's request).
- **Login screen** follows `code 3.html` (dark variant, matching the app's
  dark-only runtime) with the v1.1 PIN redesign: `#111215` page, red
  announcement banner ("Welcome to SpicyTool v1.0! SpicyTool Exclusive
  features are now live!"), `#18191d` card showing **only the logo** (72px,
  centered), the word "SpicyTool" with **Spicy in pulsing red glow and Tool
  in white** (no underline — removed at the owner's request), then a
  two-step form — email (owner addresses only) → **Continue** → masked
  6-digit **PIN** input with **Sign In** and "use a different email".
  No theme toggle (dark-only runtime), no demo path: every sign-in goes
  through the real server-side PIN check.
- **Real logo — one asset, one link.** The SpicyTool mark (`logo.png` in the
  repo root, uploaded by the owner) is cropped to the artwork, rendered at
  144px and inlined as an optimized PNG data URI (`LOGO_SRC`). That single
  constant feeds **every** place the brand appears — 72px in the login hero,
  32px in the search, results *and* itinerary headers, plus the tab
  favicon/apple-touch icon (set at boot, no second copy of the artwork in the
  HTML). In all three app headers the logo + wordmark are one real
  `<a href="/">`, i.e. the same link as the app itself, and clicking it
  always returns to the search screen.
- **Live sign-in session** — the app itself never opens on the login screen:
  browsing, results and itinerary views (including `#itinerary/<id>` in a
  fresh tab) are public. Signing in (sessionStorage token, dies with the tab)
  is only required to **run a search** — the search APIs return `401` without
  a session, which drops the user to the PIN login; a verified session swaps
  the avatar to the `code 5` gradient-ring initials and offers Sign out
  (which clears the session server-acknowledged and returns to the app). A
  **Support** button
  in both headers opens a blank compose to `adhambadraan@gmail.com`
  (`mailto:`).
- **Broker CPM pricing + "Modify programs" (code 15)** — the home-page
  "Modify programs" pill opens the dark brokers dialog listing the owner's
  **29 broker programs** (the 10 engine-searched programs plus 19 broker-only
  rows, in the owner's order and wording — Aeromexico Club Premier through
  JAL Mileage Bank): per-program **cost-per-mile** inputs (¢/mile, default
  **1.4**), program checkboxes, Deselect all / Save as default. **Cash price
  = miles × the program's CPM + taxes & fees** (round-trips price each leg
  with its own program's CPM). CPMs and program selections persist in
  `localStorage`; deselected engine programs are excluded from results
  (broker-only rows carry no engine results yet). The "Ticket via
  SpicyTool.com" option was removed at the owner's request — brokers is the
  only mode; the "Try Broad Search" promo CTA remains mockup-only.
- **Itinerary (code 13) — an in-app view, never a blank tab, and no search
  bar.** Clicking a **price tile opens the itinerary in its own new tab**
  automatically — there is no button to press. The itinerary renders
  announcement banner, the same logo header (`<a href="/">`), the stepper,
  the Retail/Cost/Discount summary, the per-leg segment timeline with layover
  notices and the Award Redemptions matrix — generated same-origin from that
  result's data, no backend round-trip. The search bar appears **only on the
  home screen**; the itinerary view has none. With no selection it shows a
  "No itinerary selected" card with a way back.
- **Itinerary links (`#results`, `#itinerary/<id>`)** — every view has a real
  URL. *Itinerary in new tab* is a plain link to `/#itinerary/<id>`; the last
  search (query + up to 80 results) is persisted to
  `sessionStorage`/`localStorage`, so a new tab, a refresh or a shared link
  renders that exact itinerary instead of a blank page (pop-up blockers can
  no longer swallow it). **Sign-in is never required for an itinerary tab** —
  the itinerary renders purely from the persisted results and makes no
  protected API call, so a fresh or incognito tab shows the flight instead of
  a login wall (signing in is only needed to *run a new search*). The
  matrix's **Flight link** is a real link to the operating airline's own site
  (`swiss.com`, `lufthansa.com`, …), and legs read "Operated by Swiss
  International Air Lines" rather than "Operated by LX".
- Hovering a fare tile shows the booking program(s) with points + taxes.
- All colors/radii/spacings/shadows are the computed equivalents of the
  mockups' Tailwind classes; fonts use the mockups' own stacks (Inter with
  system fallbacks — no external CDNs).

**Cost model (disclosed in-UI):** tile cash price = miles × your broker CPM
(per program, default 1.4¢ — see *Modify programs*) + taxes & fees; "Retail"
is the engine's modeled estimate (miles × cabin rate); savings/discount
compare the two. Retail is labeled as a modeled estimate in the itinerary's
Retail/Cost/Discount summary, and the itinerary footer reminds travellers to
always confirm on the airline's own site before booking.

## What you get

- **84 airports** (real coordinates), **39 carriers** with real hubs,
  **10 loyalty programs** with real award-chart shapes and transfer partners.
- **Owner PIN login — no OTP, no verification** — sign-in is restricted to
  exactly two addresses (`adhambadraan@icloud.com` /
  `adhambadraan@gmail.com`, the owner's accounts; configurable via
  `ALLOWED_LOGIN_EMAILS`). Step 1 checks the address; step 2 accepts the
  6-digit account **PIN** (`141220` by default, set via `LOGIN_PIN`) and
  issues a stateless HMAC-signed session token. Wrong PINs are rate-limited
  (5 attempts → 60-s lockout per address). The client keeps the session in
  `sessionStorage`, so closing the tab always ends it — reopening requires
  email + PIN again. Engine routes (`/api/v1/search*`, `/api/v1/calendar`)
  reject unauthenticated calls with `401` (`AUTH_ENFORCE=0` disables this
  for local testing only). Email delivery (Resend) is no longer part of the
  login flow.
- **Ticket types** — every result carries a deterministic ticket type with a
  points multiplier so the Tickets filter and result badges are meaningful:
  `award` (chart price), `hc` hidden-city (×0.82, 1+ stops), `upg`
  upgrade/mixed-cabin, `dis` AMEX-transfer discount (×0.95), `published`,
  `consolidator` (×0.93) and `basis_exclusive` SpicyTool-exclusive (×0.88).
  Badges render on the right-hand side of each result; round-trip pairs carry
  per-leg types.
- **Filters — exactly four groups**: Airlines (all 39 carriers in a fixed
  list, per-row "Only" + Reset), Stops (Any / Non-Stop Only / One stop or
  fewer / Two stops or fewer), Tickets (the 7 types, Only + Reset) and
  Programs (the 10 programs, Only + Reset) — every count wired to the live
  result set.
- **Super HC remaining searches** — the toggle shows the connected Flybasis
  session's monthly `maxSearchesRemaining`, e.g. “Enable super hc mode
  (3 searches remaining for this month)”. It refreshes on sign-in, after search
  attempts, and when returning to a stale page. Zero is shown explicitly;
  signed-out/unavailable quotas are labelled honestly, never guessed or stored
  in browser preferences. The counter is not available for an unrelated
  official socket-key account or a disconnected provider.
- **Passengers stepper** — Adults / Children / Infants with −/+ steppers
  (infants capped at adults; seat-taking passengers = adults + children feed
  the engine's `passengers` parameter).
- **Flexibility ±1/±2/±3 days** — the stepper fans out one real search per
  nearby date in parallel and merges the results (date-tagged) into one list.
- **Multi-airport search** — up to **3 origins × 3 destinations** per query
  (comma-separated, e.g. `origin=JFK,EWR,LGA`): every pair is fanned out in
  parallel and merged into one result stream. The UI supports in-field IATA
  chips (up to 3 per side) with a live recommendations dropdown, plus metro
  shortcuts (`NYC` → JFK+EWR+LGA, `LON`, `TYO`).
- **Round-trip search** — add `return_date=YYYY-MM-DD` (or pick Departure +
  Return in the UI's Round Trip mode): both one-way legs are searched in
  parallel and combined into round-trip itineraries. **Legs may book into
  different loyalty programs** — pairs are ranked by total points and marked
  `same_program`, with per-leg rows in the price breakdown. Filters apply to
  both legs (e.g. Nonstop = nonstop both ways).
- **Airline logos — real artwork for all 39 carriers, zero placeholders.**
  Every carrier renders its **official full-colour mark** as a 24×24 image on
  a white tile — SWISS is the red square with the white cross, Lufthansa the
  crane in a circle, KLM the crown, Emirates the calligraphy — identical
  across the result cards, the expanded timeline, the itinerary legs and the
  new tab. Artwork is loaded from the same public sources flight-search sites
  use (`www.gstatic.com/flights/airline_logos/70px/<IATA>.png`, then
  `pics.avs.io/200/200/<IATA>.png`); if a code is missing there, or the
  browser is offline, the logo falls back to the built-in brand tile (the
  carrier's official glyph in SVG on its brand colour, e.g. `LX` white cross
  on Swiss red) — so the slot is never empty and never a broken image.
  Unknown future codes fall back to a deterministic hashed-colour tile.
  Carrier names (`CARRIER_NAMES`) and official sites (`CARRIER_SITES`) ship
  with the same table.
- Deterministic first-party engine: the same query always returns the same
  results; different dates differ.
- **Carrier network (39)** — Aegean, Aer Lingus, Air Canada, Air Dolomiti,
  Air Europa, Air France, Air Serbia, American, Austrian, Avianca, British
  Airways, Brussels, Condor, Croatia, Delta, Discover (4Y), Egyptair,
  Emirates, Ethiopian, Etihad, Eurowings, Finnair, flyDubai, Iberia,
  Icelandair, ITA, JetBlue, KLM, LOT, Lufthansa, Lufthansa City (VL), Royal
  Air Maroc, Royal Jordanian, SAS, Swiss, TAP, Turkish, United, Virgin
  Atlantic. Modeling notes: SAS is SkyTeam (2024 move); Virgin Atlantic is a
  Delta JV partner rather than a SkyTeam member; Lufthansa-group regionals
  (Air Dolomiti, Eurowings, Discover, Lufthansa City) are modeled as
  Star-Alliance-bookable because their metal sells under LH group awards.
- **Programs (10)** — Aeroplan, Flying Blue, Alaska Mileage Plan, AAdvantage,
  SkyMiles, Etihad Guest, Qantas Frequent Flyer, TAP Miles&Go, Miles&Smiles,
  MileagePlus. Non-alliance partners are honored via a
  `partner_carriers()` hook: Aeroplan↔Aer Lingus, Alaska↔Condor/Icelandair,
  SkyMiles↔Virgin Atlantic, Etihad Guest↔Air Serbia, Qantas↔Emirates/flyDubai,
  MileagePlus↔JetBlue (Blue Sky), Miles&Smiles↔Air Serbia.
- **v1 API** — first-party engine: search, SSE streaming search (one event per
  program), airport typeahead, program inventory, 30-day flexible-date
  calendar.
- **v2 API** — aggregation layer over five providers
  (`SpicyToolEngine` always on; `AwardTool`, `PointsPath`, `PointsYeah`,
  `Flybasis` credential-gated), cross-provider dedupe, provider diagnostics,
  cache stats, telemetry firewall report.
- **Streaming everywhere** — results render the millisecond a provider
  resolves; repeat queries are cache hits (~1.7 s → ~0 ms).

## API surface (Swagger at `/docs`)

| Path | What it does |
|---|---|
| `GET /` | the frontend (same origin as the API) |
| `POST /api/v1/auth/check` | step 1: is the address one of the two owner emails? |
| `POST /api/v1/auth/login` | step 2: email + PIN → session token (5 wrong tries → 60-s lockout) |
| `GET /api/v1/auth/session` | validate a token |
| `POST /api/v1/auth/logout` | client discards the session token |
| `GET /api/v1/health` | `{status, airports: 84, programs: 10}` |
| `GET /api/v1/airports?q=&limit=` | ranked typeahead |
| `GET /api/v1/programs` | 10 programs + colors + transfer banks |
| `GET /api/v1/search` 🔒 | first-party award search |
| `GET /api/v1/search/stream` 🔒 | SSE, one event per program |
| `GET /api/v1/calendar` 🔒 | cheapest award per day (1–60 days) |
| `GET /api/v2/providers` | provider inventory + gating reasons |
| `GET /api/v2/super-hc/quota` 🔒 | private, uncached monthly Super HC remaining count; `null`/`available:false` when unknown |
| `GET /api/v2/telemetry` | blocklist + blocked-request counter |
| `GET /api/v2/cache/stats` | cache backend, hits/misses, TTL |
| `GET /api/v2/search` | aggregated, deduped search |
| `GET /api/v2/search/stream` | SSE `start → data* → complete` |
| `GET /api/v2/context` | general web context (AgentSearch when keyed, else the keyless MCP connector) — always `is_award_data: false` |
| `GET /api/v2/context/status` | which web-search backend is connected, and how the key was resolved |

🔒 = requires the session token (`Authorization: Bearer …` header or
`?token=` for `EventSource`). The token is minted only through the PIN flow;
tests mint tokens in-process against the same per-install signing secret
(`backend/data/.auth_secret`, auto-generated, git-ignored; override with
`AUTH_SECRET`). On read-only/ephemeral filesystems (serverless) the secret
is derived deterministically from the login config, so a session survives
cold starts instead of logging the user out mid-use.

Validation: unknown IATA → `400 "Unknown origin 'XXX'"`; same origin and
destination → `400`; bad cabin → `400`; date must match `^\d{4}-\d{2}-\d{2}$`;
more than 3 airports per side → `400 "At most 3 origin airports"`;
`return_date` before `date` → `400 "Return date must be on or after the
departure date"`.

> ⚠️ **Rotate the exposed key.** A real RapidAPI key was committed to this
> public repo in `bbd85a2`. See [`ROTATE_KEY.md`](ROTATE_KEY.md) for the
> zero-downtime rotation steps.

## Verifying live, on GitHub Actions

This sandbox has no general outbound internet (SNI-filtered allowlist) and no
Flybasis credential, so the `--live` check cannot run (or be faked) from here.
A GitHub Actions runner *does* have full access, and the workflow at
`ci/live-check.workflow.yml` runs the whole suite — on every push, weekly, and
manually from the Actions tab — including the real calls to
`enterprise-api.flybasis.com` and, if configured, `agentsearch.p.rapidapi.com`.

The workflow never fabricates live results. Each live gate is credential-gated:
it is **skipped** while its secret is absent and runs *for real* the moment the
secret exists:

```bash
# Settings -> Secrets and variables -> Actions -> New repository secret
#    Name: FLYBASIS_API_KEY      (REQUIRED for live award results — token issued
#                                 to you by Flybasis; see FLYBASIS_GO_LIVE.md)
#    Name: AGENTSEARCH_API_KEY   (OPTIONAL — RapidAPI key for web context only;
#                                 never an award feed)
```

**Install it (one-time, owner only):** GitHub refuses pushes from a GitHub App
without the `workflows` permission, so the App cannot move this file into
`.github/workflows/` itself:

```bash
mkdir -p .github/workflows
git mv ci/live-check.workflow.yml .github/workflows/live-check.yml
git commit -m "Install live-check workflow" && git push
```

With a secret set, the gate fails loudly on a rejected key, an exhausted quota,
or upstream schema drift — an ongoing contract test against the real API. The
local, keyless suite (all offline gates above plus the Flybasis mock socket) is
what runs green in CI before any live credential exists.

## Verifying the AgentSearch integration

The web-context backend has a self-contained verifier that exercises the real
client over real HTTP — no outbound network needed:

```bash
cd backend
../.venv/bin/python tools/verify_agentsearch.py          # offline: boots a local
                                                          # mock of the documented
                                                          # /v1 schema
AGENTSEARCH_API_KEY=<key> \
  ../.venv/bin/python tools/verify_agentsearch.py --live  # real RapidAPI endpoint
```

Both modes run the same five assertions: `/v1/search` returns the documented
envelope, `/v1/answer` and `/v1/fetch` work, web context is labelled
`is_award_data:false`, and award search stays isolated from the web backend.
`tools/agentsearch_mock.py` serves the published schema byte-for-byte and is
dev-only — the app never imports it.

## Configuration (`.env`)

| Variable | Default | Meaning |
|---|---|---|
| `AWARDTOOL_API_KEY` | *(blank)* | enables the AwardTool adapter |
| `POINTSPATH_API_KEY` | *(blank)* | enables the PointsPath adapter |
| `POINTSYEAH_API_KEY` | *(blank)* | enables the PointsYeah adapter |
| `SEATS_AERO_API_KEY` | *(blank)* | enables the **Seats.aero** partner API (`SPICYTOOL_PROVIDERS=SeatsAero`): cached award availability for ~20 mileage programs — points, seats, cabins, airlines, flights. Key from [seats.aero/settings](https://seats.aero/settings) (API tab; Pro account, up to 1,000 calls/day, non-commercial unless you have a written agreement). `SEATS_AERO_BASE_URL` overrides the host for tests/mocks. |
| `FLYBASIS_API_KEY` | *(blank)* | enables the Flybasis adapter (Socket.IO award feed, see `Flybasis-index.md`). Issued **by Flybasis** to the operator — see [`FLYBASIS_GO_LIVE.md`](FLYBASIS_GO_LIVE.md) for the exact steps, verification, and private HAR import. |
| `FLYBASIS_SUPABASE_URL` · `FLYBASIS_SUPABASE_ANON_KEY` · `FLYBASIS_REFRESH_TOKEN` (or `FLYBASIS_EMAIL`+`FLYBASIS_PASSWORD`) | *(blank)* | **Session mode (advanced):** used when no official award-feed key is configured (a misplaced RapidAPI key is ignored by award auth). Same feed via your own Flybasis account session — Supabase exchange → access token → socket auth. Consumes your account quota and may conflict with Flybasis ToS; see [`FLYBASIS_GO_LIVE.md`](FLYBASIS_GO_LIVE.md) § Option B. Never commit these. |
| `AGENTSEARCH_API_KEY` | *(blank)* | RapidAPI key for the [AgentSearch](https://rapidapi.com) web-search API. **Web context only — not an award feed.** When set it becomes the preferred backend for the "Web context" panel (`/api/v2/context`), with the keyless FlyBasis Search MCP connector as automatic fallback. If `FLYBASIS_API_KEY` is set to a RapidAPI-shaped key (`…msh…jsn…`) it is used here automatically and is **not** dialled at the Flybasis award socket. |
| `AGENTSEARCH_PROVIDER` | `brave` | SERP provider AgentSearch proxies (`brave` or `serper`) |
| `AGENTSEARCH_MCP_URL` | *(blank)* | optional MCP fallback, e.g. `https://agentsearch-mcp.vercel.app/mcp` — same 3-tool surface as the keyless FlyBasis connector |
| `AGENTSEARCH_COUNTRY` | `us` | country bias for AgentSearch results |
| `AGENTSEARCH_TIMEOUT` | `12` | seconds; web search is slower than an award lookup |
| `REDIS_URL` | `redis://localhost:6379/0` | cache; falls back to memory if unreachable |
| `CACHE_TTL` | `2700` | seconds, clamped to the 30–60 min band |
| `PROVIDER_TIMEOUT` | `3.5` | per-request budget for third-party providers |
| `LOGIN_PIN` | `141220` | the account PIN for the two owner emails (set it in `backend/.env`, git-ignored) |
| `ALLOWED_LOGIN_EMAILS` | `adhambadraan@icloud.com,adhambadraan@gmail.com` | comma-separated; the only addresses that can sign in |
| `AUTH_SECRET` | *(generated file)* | HMAC secret for session tokens |
| `AUTH_ENFORCE` | `1` | `0` disables login enforcement (local tests only) |

Every third-party adapter is **inert until an operator supplies a credential
issued to them by that provider**. A disabled provider reports a clear,
actionable reason and never breaks a request. When a provider *is* configured
and the upstream socket fails, the **exact `ProviderError` is surfaced** in the
API response (`providers[].error`) — it is never masked. `.env` is loaded from
the repo root and `backend/.env` (both git-ignored); `python-dotenv` is now in
both requirements manifests so the documented `backend/.env` path actually
works on local runs as well as `docker compose`/Vercel env vars.

**Flybasis award search vs. the web-context connector:** award availability is
exclusively `backend/providers/flybasis.py` (Socket.IO award feed). The
`flybasis-mcp/` folder is a **separate, keyless** web-context MCP connector
(`web_search` / `instant_answer` / `fetch_url`) — it supplies the "Web context"
panel and can never produce flights. Its deployment
(`https://flybasis-mcp.vercel.app/mcp`) previously died (`DEPLOYMENT_NOT_FOUND`);
redeploy it keylessly from `flybasis-mcp/` (`npx vercel --prod`) and point
`FLYBASIS_MCP_URL` at the new URL. Everything it needs is in the repo — no
RapidAPI, no proxy secret.

## Design principles

1. **No bot-protection evasion** — no UA rotation, no header forgery, no
   session replay, no CAPTCHA solving, no proxy rotation. Third-party adapters
   authenticate only with documented bearer/API-key headers and a single
   stable, honest, self-identifying User-Agent.
2. **Credential gating** — never hardcoded; disabled providers explain why.
3. **No fabricated live inventory** — chart-accurate pricing, modeled seats,
   surfaced honestly in the API and UI.
4. **Telemetry isolation** — 50 analytics/beacon hosts are answered with a
   synthetic `204` and **never dialed** (no DNS, no TCP, no TLS, no egress).

## Layout

```
├── docker-compose.yml / run.sh / .env.example
├── frontend/index.html      # Obsidian Crimson UI, zero external CDNs
└── backend/
    ├── main.py              # app + v1 routes + static mount + lifecycle
    ├── api_v2.py            # v2 aggregation router
    ├── tests_integration.py # offline assertion suite
    ├── data/                # airports.json (84), transfer_matrix.json
    ├── core/                # geo, network, itinerary, pricing, schema,
    │                        # cache, redis_cache, http_engine
    ├── adapters/            # 10 loyalty-program adapters (v1)
    ├── providers/           # base, enrich, local_engine + 3 gated adapters
    └── services/            # orchestrator, transfer_calculator,
                             # aggregator, dedupe
```

See **ARCHITECTURE.md** for the request lifecycle and failure model, and
**adapters_README.md** for adding your own provider.
