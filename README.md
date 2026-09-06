# SpicyTool 🔥

Free, login-free award-flight search. No accounts, no API keys required from
end users, no paywall, no tracking. Returns real, useful results out of the
box, while exposing a provider-agnostic aggregation layer that authorized
commercial feeds can drop into unchanged.

**Stack:** Python 3.12 · FastAPI · AsyncIO · HTTPX · Pydantic v2 · sse-starlette · Redis

> **Honest data provenance.** No airline publishes a public award-availability
> API. Points totals and taxes are **chart-accurate**; **seat availability is
> modeled, not live**. Always confirm on the airline's own site before booking.

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
cd backend && python3 tests_integration.py   # 16 assertions, offline
```

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
  tile, "$X Off Retail" savings badge, dashed timeline expansion and the
  `w-80` price-breakdown popover with the red **Get VI\*** CTA.
- **Login screen** follows `code 3.html` (dark variant, matching the app's
  dark-only runtime) with the v1.0 OTP redesign: `#111215` page, red
  announcement banner ("Welcome to SpicyTool v1.0! SpicyTool Exclusive
  features are now live!"), `#18191d` card showing **only the logo** (72px,
  centered), the word "SpicyTool" over an **animated red-glow underline**,
  then a two-step form — email (`name@bcflights.com`, validated) → **Send
  Code** → 6-digit OTP input with **Verify & Sign In**, resend link (60-s
  cooldown) and "use a different email". No theme toggle (dark-only runtime),
  no demo path: every sign-in goes through the real Resend OTP flow.
- **Real logo** — the SpicyTool mark (`logo.png` in the repo root, uploaded
  by the owner) is cropped to the artwork, rendered at 144px and inlined as
  an optimized PNG data URI: 72px in the login hero, 32px in both app
  headers.
- **Live sign-in session** — the app opens on the OTP login; a verified
  session (sessionStorage token, dies with the tab) unlocks the app, swaps
  the avatar to the `code 5` gradient-ring initials and offers Sign out
  (which clears the session server-acknowledged and returns to login). A
  `401` anywhere drops the user back to the login view. A **Support** button
  in both headers opens a blank compose to `adhambadraan@gmail.com`
  (`mailto:`).
- **Broker CPM pricing + "Modify programs" (code 15)** — the home-page
  "Modify programs" pill opens the dark brokers dialog: per-program
  **cost-per-mile** inputs (¢/mile, default **1.4**), program checkboxes,
  Deselect all / Save as default. **Cash price = miles × the program's CPM +
  taxes & fees** (round-trips price each leg with its own program's CPM).
  CPMs and program selections persist in `localStorage`; deselected programs
  are excluded from results. The "Ticket via SpicyTool.com" option is
  mockup-only (red "coming soon" toast), as is the "Try Broad Search" promo
  CTA.
- **Itinerary page (code 13)** — clicking a fare tile opens a full itinerary
  page in a new tab (and the price popover's **Get VI\*** opens the same page
  as a pop-up window): announcement bar, live header, stepper,
  Retail/Cost/Discount summary, per-leg segment timeline with layover
  notices, and the Award Redemptions matrix — generated same-origin from that
  result's data, no backend round-trip. Hovering a fare tile shows the
  booking program(s) with points + taxes.
- All colors/radii/spacings/shadows are the computed equivalents of the
  mockups' Tailwind classes; fonts use the mockups' own stacks (Inter with
  system fallbacks — no external CDNs).

**Cost model (disclosed in-UI):** tile cash price = miles × your broker CPM
(per program, default 1.4¢ — see *Modify programs*) + taxes & fees; "Retail"
is the engine's modeled estimate (miles × cabin rate); savings/discount
compare the two. Both figures are labeled as modeled in the price popover and
the itinerary page.

## What you get

- **84 airports** (real coordinates), **39 carriers** with real hubs,
  **10 loyalty programs** with real award-chart shapes and transfer partners.
- **Email-OTP login** — sign-in is restricted to `name@bcflights.com`
  addresses; a 6-digit code is delivered by the **Resend** API (10-min expiry,
  60-s resend cooldown, 5-attempt limit) and exchanged for a stateless
  HMAC-signed session token. The client keeps the session in
  `sessionStorage`, so closing the tab always ends it — reopening requires
  email + OTP again. Engine routes (`/api/v1/search*`, `/api/v1/calendar`)
  reject unauthenticated calls with `401` (`AUTH_ENFORCE=0` disables this for
  local testing only). **Note:** this dev sandbox has no outbound network
  access to `api.resend.com`, so OTP delivery returns a clear `503` here;
  deployed instances deliver normally.
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
- **Airline logos — all 39 carriers covered.** 16 carriers render their
  official brand glyph (inline SVG, simple-icons CC0, brand-color tiles,
  luminance-aware contrast); 14 more render vector marks in their own brand
  colors on white tiles (soaring-symbols collection — incl. Aegean, Aer
  Lingus, Air Dolomiti, Air Europa, Air Serbia, Eurowings, Icelandair, LOT,
  Royal Air Maroc, JetBlue); the last 9 (Egyptair, Austrian, Royal Jordanian,
  Condor, Croatia, Discover, flyDubai, ITA, Lufthansa City) use official
  raster wordmarks (Daisycon airline-logo feed) on white tiles. Unknown
  future codes still fall back to monogram tiles.
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
- **v2 API** — aggregation layer over four providers
  (`SpicyToolEngine` always on; `AwardTool`, `PointsPath`, `PointsYeah`
  credential-gated), cross-provider dedupe, provider diagnostics, cache stats,
  telemetry firewall report.
- **Streaming everywhere** — results render the millisecond a provider
  resolves; repeat queries are cache hits (~1.7 s → ~0 ms).

## API surface (Swagger at `/docs`)

| Path | What it does |
|---|---|
| `GET /` | the frontend (same origin as the API) |
| `POST /api/v1/auth/request-otp` | email a 6-digit code (bcflights.com only, Resend delivery) |
| `POST /api/v1/auth/verify-otp` | exchange the code for a session token |
| `GET /api/v1/auth/session` | validate a token |
| `POST /api/v1/auth/logout` | client discards the session token |
| `GET /api/v1/health` | `{status, airports: 84, programs: 10}` |
| `GET /api/v1/airports?q=&limit=` | ranked typeahead |
| `GET /api/v1/programs` | 10 programs + colors + transfer banks |
| `GET /api/v1/search` 🔒 | first-party award search |
| `GET /api/v1/search/stream` 🔒 | SSE, one event per program |
| `GET /api/v1/calendar` 🔒 | cheapest award per day (1–60 days) |
| `GET /api/v2/providers` | provider inventory + gating reasons |
| `GET /api/v2/telemetry` | blocklist + blocked-request counter |
| `GET /api/v2/cache/stats` | cache backend, hits/misses, TTL |
| `GET /api/v2/search` | aggregated, deduped search |
| `GET /api/v2/search/stream` | SSE `start → data* → complete` |

🔒 = requires the session token (`Authorization: Bearer …` header or
`?token=` for `EventSource`). The token is minted only through the OTP flow;
tests mint tokens in-process against the same per-install signing secret
(`backend/data/.auth_secret`, auto-generated, git-ignored; override with
`AUTH_SECRET`).
| `GET /api/v2/providers` | provider inventory + gating reasons |
| `GET /api/v2/telemetry` | blocklist + blocked-request counter |
| `GET /api/v2/cache/stats` | cache backend, hits/misses, TTL |
| `GET /api/v2/search` | aggregated, deduped search |
| `GET /api/v2/search/stream` | SSE `start → data* → complete` |

Validation: unknown IATA → `400 "Unknown origin 'XXX'"`; same origin and
destination → `400`; bad cabin → `400`; date must match `^\d{4}-\d{2}-\d{2}$`;
more than 3 airports per side → `400 "At most 3 origin airports"`;
`return_date` before `date` → `400 "Return date must be on or after the
departure date"`.

## Configuration (`.env`)

| Variable | Default | Meaning |
|---|---|---|
| `AWARDTOOL_API_KEY` | *(blank)* | enables the AwardTool adapter |
| `POINTSPATH_API_KEY` | *(blank)* | enables the PointsPath adapter |
| `POINTSYEAH_API_KEY` | *(blank)* | enables the PointsYeah adapter |
| `REDIS_URL` | `redis://localhost:6379/0` | cache; falls back to memory if unreachable |
| `CACHE_TTL` | `2700` | seconds, clamped to the 30–60 min band |
| `PROVIDER_TIMEOUT` | `3.5` | per-request budget for third-party providers |
| `RESEND_API_KEY` | *(blank — required)* | Resend API key for OTP email delivery (set it in `backend/.env`, git-ignored, or the deployment environment; never commit it — GitHub blocks secret pushes) |
| `RESEND_FROM` | `SpicyTool <noreply@bcflights.com>` | OTP sender address |
| `AUTH_SECRET` | *(generated file)* | HMAC secret for session tokens |
| `AUTH_ENFORCE` | `1` | `0` disables login enforcement (local tests only) |

Every third-party adapter is **inert until an operator supplies a credential
issued to them by that provider**. A disabled provider reports a clear,
actionable reason and never breaks a request.

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
    ├── tests_integration.py # 16 assertions
    ├── data/                # airports.json (78), transfer_matrix.json
    ├── core/                # geo, network, itinerary, pricing, schema,
    │                        # cache, redis_cache, http_engine
    ├── adapters/            # 10 loyalty-program adapters (v1)
    ├── providers/           # base, enrich, local_engine + 3 gated adapters
    └── services/            # orchestrator, transfer_calculator,
                             # aggregator, dedupe
```

See **ARCHITECTURE.md** for the request lifecycle and failure model, and
**adapters_README.md** for adding your own provider.
