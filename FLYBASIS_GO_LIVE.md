# FLYBASIS GO-LIVE — everything needed is in the repo, except one thing

**Final verdict (verified 2026-09-06):**

| Item | State |
|---|---|
| Award-feed adapter (`backend/providers/flybasis.py`) | ✅ in repo, wired, merged, passing 27/27 acceptance checks |
| Flybasis WebSocket API docs (`Flybasis-index.md`) | ✅ in repo |
| Exact `ProviderError` surfacing (never hidden) | ✅ verified — returns `Flybasis connection failed: …` verbatim |
| `backend/.env` loading | ✅ **fixed this session** (`python-dotenv` was missing from requirements) |
| `flybasis-mcp` (web context connector) | ✅ **rebuilt keyless + self-contained in repo** (`flybasis-mcp/`) |
| `FLYBASIS_API_KEY` | ❌ **NOT in the repo** — never was. Only placeholders (`YOUR_AUTH_TOKEN`) exist. |

The one input no repo, no agent, and no prompt can conjure is the **Flybasis
API key**. It is issued by Flybasis to their partner/operator and the docs say
only "once an authentication token is obtained…" — there is no public
self-serve page (flybasis.com is behind Cloudflare and shows no key portal; the
docs at https://flybasis.github.io/searchapi.docs/ confirm the token comes from
their middleware). Per the project's hard constraint #2, credentials are
**never hardcoded** — so the honest product state without a key is the current
empty state, and synthesized flights are explicitly rejected.

## How to make award search show live results (2 steps, ~2 minutes)

### Step 1 — get the key from Flybasis

Contact Flybasis to obtain an API token for the WebSocket feed
`https://enterprise-api.flybasis.com/sockets/v1/stream-flights`:

- Partner/API inquiry: https://flybasis.com (site), or the docs repo
  https://github.com/flybasis/searchapi.docs (auth section).

**Verified contact channels (checked 2026-09-06 — use these, not the site form):**

- `support@flybasis.com` — published on Flybasis' Terms of Service page.
- `asad@flybasis.com` — GitHub profile email of the maintainer who owns the API
  docs repo (`github.com/asadukashif`, company `flybasis.com`; he authored the
  docs commits). Best route for WebSocket API access specifically.
- `https://github.com/flybasis` — official org (only the docs repo is public).

**What a thorough online search found (and did NOT find):** no public or demo
token anywhere; no sandbox; no signup/self-serve portal; no RapidAPI listing;
and GitHub code search shows **zero** third-party integrations of
`enterprise-api.flybasis.com` — the only public hit is Flybasis' own docs.
The docs' `YOUR_AUTH_TOKEN` is a placeholder, not a key: it is genuinely
issued by Flybasis to partners.

**Access request already filed on their behalf (2026-09-06):**
https://github.com/flybasis/searchapi.docs/issues/1 — a public API-access
request on the official docs repo asking how to obtain a token, whether a
sandbox exists, and pricing. Check that thread for a reply before doing
anything else. (The old `flybasis.com/agency-request` page that advertised
"utilize our API" for agency accounts now returns 404; the archived copy
shows it was a travel-agency consolidation application — agency identity and
volume, not a generic developer signup.)

- The token is what you pass as `auth={"token": "<KEY>"}` when connecting.

The key must be **issued to you directly by Flybasis**. I cannot generate it,
and inventing one is the one thing I refuse to do (it would fake live
availability).

### Step 2 — set it, then verify

Local run (repo root — works with either file):

```bash
cd /home/user/SpicyTool   # your clone
printf 'FLYBASIS_API_KEY=PASTE_YOUR_KEY_HERE\n' > .env    # or backend/.env
./run.sh                                                   # loads .env
```

Deployed (Vercel):

```bash
# Vercel dashboard → Project → Settings → Environment Variables
FLYBASIS_API_KEY = <paste>
# also SPICYTOOL_PROVIDERS=Flybasis (default) and SPICYTOOL_MODELED_ENGINE=0 (default)
```

CI (GitHub Actions → Settings → Secrets and variables → Actions):
`FLYBASIS_API_KEY` — the installed `.github/workflows/live-check.yml` then runs
the same live verifier against production on every push and weekly
(`python tools/verify_flybasis_socket.py --live`). Until the secret exists the
step is skipped honestly, never faked.

Verify (no auth needed for v2):

```bash
curl 'http://localhost:8000/api/v2/search?origin=JFK&destination=LHR&date=2026-10-05&cabin=business'
# EXPECT: providers[0].ok == true, count > 0, live == true, notice == null
```

The moment Flybasis returns real frames you will see itineraries. If the socket
errors, the response now carries the **exact** ProviderError
(`providers[0].error`), e.g. `Flybasis connection failed: <reason>` or
`Flybasis error event: <message>` — sent as given by the server.

### Alternative live source you can get TODAY — Seats.aero

Flybasis is not the only way to get real award availability. The repo now
ships a full adapter for the **Seats.aero partner API**
(`backend/providers/seats_aero.py`): cached award availability across ~20
mileage programs with points, seats, cabins, airlines and flight-level detail
(OpenAPI: https://developers.seats.aero).

```bash
# 1. Seats.aero Pro account -> settings -> API tab -> generate API key
#    (up to 1,000 calls/day; non-commercial unless you have their written
#     agreement; eligibility is at their sole discretion)
# 2. Point SpicyTool at it (never commit the key):
printf 'SEATS_AERO_API_KEY=<your key>\n' >> .env
SPICYTOOL_PROVIDERS=SeatsAero ./run.sh
# 3. Verify:  curl http://localhost:8000/api/v2/search?origin=JFK&destination=LHR&date=2026-10-05&cabin=business
#    EXPECT: providers[] includes SeatsAero with ok == true and count > 0
```

Why this matters: it is a **legitimate, documented, self-serve** developer
feed — no invitation, no middleware-hidden token. The one input it also
requires is a key from *your own* Seats.aero account (identity + paid Pro
tier, so it cannot be conjured by an agent). AwardSecrets
(https://awardsecrets.com) is another dev-oriented option ($0.02/search,
onboarding by email) but returns seat facts **without points pricing**, so it
does not fill SpicyTool's award-pricing cards.

### Prove the socket works BEFORE you have a key

`FLYBASIS_BASE_URL` (optional, mirrors `AGENTSEARCH_BASE_URL`) points the award
socket at any host, so the whole live path can be exercised without a
credential and without outbound network:

```bash
cd backend && ../.venv/bin/python tools/verify_flybasis_socket.py
# 9 checks over a REAL websocket against tools/flybasis_mock.py: connect + auth
# payload, the documented `search` body, stops/layover/mixed-cabin/bookability
# normalization, both directions of a round trip, `error` events surfaced
# verbatim, a silent upstream failing loudly inside its budget, a rejected
# token reading as auth failure (never as "no results"), and the provider cache.
```

If that passes, the ONLY thing between you and live results is the key — which
is the answer to "search returns nothing". To confirm a real key on a real
endpoint, the same checks run against production:

```bash
FLYBASIS_API_KEY=<your key> ../.venv/bin/python tools/verify_flybasis_socket.py --live
```

### Also redeploy the web-context connector (free, no keys)

`https://flybasis-mcp.vercel.app/mcp` is dead (`DEPLOYMENT_NOT_FOUND`). The
connector is now **fully keyless**, so redeploy it with zero secrets:

```bash
cd flybasis-mcp
npm install
npx vercel --prod        # or import the folder at https://vercel.com/new
# then in the backend env:
FLYBASIS_MCP_URL=https://<your-project>.vercel.app/mcp
```

Everything the connector needs is in `flybasis-mcp/` (tools, parsers, SSRF
guard, deploy config, offline tests).

---

## Copy-paste prompt for the next chat

> You are continuing work on the SpicyTool repo (branch with the latest
> commit that includes the keyless flybasis-mcp rebuild and the python-dotenv
> fix). Verified facts:
> 1. Award availability comes ONLY from `backend/providers/flybasis.py`
>    (Socket.IO feed `https://enterprise-api.flybasis.com/sockets/v1/stream-flights`,
>    `auth={"token": <FLYBASIS_API_KEY>}`, reads `data.awd` flight lists). It is
>    wired and merged. `flybasis-mcp/` is a SEPARATE keyless web-context
>    connector and CANNOT produce award flights — never derive itineraries from it.
> 2. `FLYBASIS_API_KEY` is NOT in the repo (by design — credential-gating
>    constraint). The owner of this repo has access to it; you do not; do not
>    fabricate a key or synthesize flights.
> 3. The user must obtain the key from Flybasis (docs:
>    https://flybasis.github.io/searchapi.docs/ — token is issued by their
>    middleware; no public self-serve portal).
> 4. `backend/.env` loading now works (python-dotenv added to
>    backend/requirements.txt and requirements.txt; run.sh also sources
>    backend/.env). Provider errors already surface verbatim via
>    `providers[].error` (verified with a dummy key: `Flybasis connection
>    failed: Connection error`).
>
> Actions:
> - If the user provides `FLYBASIS_API_KEY`: put it in `.env` (never commit),
>   start with `SPICYTOOL_PROVIDERS=Flybasis SPICYTOOL_MODELED_ENGINE=0`, run a
>   real search, and confirm `providers[].ok == true` with live results. If the
>   socket errors, surface the exact `ProviderError` and do not mask it.
> - If the user has NOT obtained the key: do not reuse this chat's workarounds;
>   state plainly that live award results require the Flybasis-issued key and
>   stop. Keep the empty state honest.
> - Quality gate: `cd flybasis-mcp && npm run test:offline` must pass, and
>   `bash acceptance_check.sh` (server with
>   `SPICYTOOL_PROVIDERS=all SPICYTOOL_MODELED_ENGINE=1`) must finish with
>   **0 failed** — it grows over time, so gate on "0 failed", not on a count.
