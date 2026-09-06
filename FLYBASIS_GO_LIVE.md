# Connect live Flybasis award search

SpicyTool has two Flybasis authentication paths: an official award-feed token,
or an authorized account's Supabase session. Credentials belong in private
server-side configuration, **not in source code, a public HAR, or the frontend**.
Deploying the code without credentials does not connect an upstream provider.

## What the supplied HAR tells us

`agentsearch.vercel.app` is a Flybasis web client. Its login capture contains:

- `POST https://sb.flybasis.com/auth/v1/verify` / `auth/v1/token`: a Supabase
  `apikey` request header and session tokens in the successful response.
- `POST https://api2.flybasis.com/trpc/user.whoami?batch=1`: the account request
  uses **`access-token: <access token>`**, with an empty JSON body `{}`.

This is **not** `agentsearch.p.rapidapi.com`, the unrelated general web-search
backend. A RapidAPI key cannot authenticate the Flybasis award socket. A
misplaced RapidAPI key is used only for web context and no longer blocks a
separately configured Flybasis session.

The supplied capture has no award-flight frames. Its login responses establish
an auth request shape, not that a captured token is still valid or permitted to
use the enterprise award feed. WebSocket access and availability require a live
check. Offline mocks cannot establish those facts.

**Publicly uploaded session tokens are exposed.** Revoke the exposed sessions
through the account/provider, then sign in again and create a new private
capture. See [ROTATE_KEY.md](ROTATE_KEY.md). Removing the HAR from the current
branch does not remove copies in Git history. Do not post replacement tokens
or another unredacted capture on GitHub or in chat.

## Option A — official award-feed token (preferred for production)

Obtain a token from Flybasis for
`https://enterprise-api.flybasis.com/sockets/v1/stream-flights`
([upstream docs](https://flybasis.github.io/searchapi.docs/)). Configure it in
Vercel Environment Variables, or a gitignored root/backend `.env` locally:

```dotenv
FLYBASIS_API_KEY=<your private Flybasis-issued token>
SPICYTOOL_PROVIDERS=Flybasis
SPICYTOOL_MODELED_ENGINE=0
```

An official token takes precedence over session credentials. No Supabase app
key is needed for this path. A RapidAPI application key is not an official
Flybasis token.

## Option B — your authorized Flybasis account session

Only use an account you own or are authorized to operate. Session mode consumes
its search quota; confirm that automated use is permitted by your agreement
with Flybasis. A session login does not guarantee enterprise API access.

### Private HAR import (no manual token copying)

After signing in, export a **private**, local HAR with response bodies. Then,
from the repo root:

```bash
# Inspect the credential structure without printing values or writing anything:
python3 backend/tools/import_flybasis_har.py /path/to/private.har --check

# Create a private repo-root .env (0600). Refuses to overwrite an existing file:
python3 backend/tools/import_flybasis_har.py /path/to/private.har
```

If `.env` already exists, use `--output backend/.env` (also automatically loaded
by the app), or `--output .env.flybasis` and merge/import its values privately.
**Other `.env.*` names are not auto-loaded.** Existing environment variables
still take precedence when Python loads dotenv files.

The importer selects the latest complete successful auth **response**, even
when HAR entries are out of order. It never imports the request's refresh token
(which was already used), an expiring access token, telemetry, or account
profile details. It rejects foreign auth hosts, mixed-account captures and
unsafe dotenv values. It makes **no network requests** and does not validate
current credentials. HARs, private env files and token stores are excluded from
the Vercel function bundle; none of this material needs to be committed.

Equivalent manual settings, entered privately:

```dotenv
FLYBASIS_SUPABASE_URL=https://sb.flybasis.com
FLYBASIS_SUPABASE_ANON_KEY=<apikey request header>
FLYBASIS_REFRESH_TOKEN=<latest successful auth RESPONSE refresh_token>
# Alternative to a refresh-token bootstrap:
# FLYBASIS_EMAIL=<authorized account email>
# FLYBASIS_PASSWORD=<account password>
```

The Supabase app key is required alongside either account credential. Without
it, the adapter stays disabled rather than making requests with a blank key.
Do not put a short-lived access token into `FLYBASIS_API_KEY` as a workaround.

### Rotation and deployment limits

The access token is cached until 30 seconds before expiry. Concurrent searches
in one instance/event loop share a refresh. The newest refresh token is kept
in memory and persisted atomically, with mode 0600, to
`backend/data/.flybasis_refresh_token` (override with `FLYBASIS_REFRESH_FILE`).
The store is bound to the original configuration: keeping the same env seed
allows a single-instance restart to use the latest rotation; replacing the env
credential invalidates the old account's cache. A failed file write no longer
causes the warm process to reuse an already-spent env token.

**Vercel/replicas:** `/tmp/spicytool/.flybasis_refresh_token` is writable but
instance-local and ephemeral. This is **not shared durable storage**. Do not
share a single refresh-token seed across browser, CLI and multiple deployments.
A local verification exchanges/rotates it too; the original `.env` seed is not
then a fresh deployment credential. Prefer an official API key for production.
Where permitted, email/password can bootstrap independent sessions on cold
starts; otherwise session deployments need a single persistent instance or a
separately managed, shared refresh service. The current file store does not
provide distributed refresh coordination.

For Vercel, set credentials through **Project → Settings → Environment
Variables**, select the relevant environments, and redeploy. GitHub repository
secrets alone do not configure the Vercel app. `PROVIDER_TIMEOUT` controls the
whole provider request; if the upstream needs longer than the default budget,
set a value such as `45` while keeping it below the 60-second function limit.

## Verify without confusing mocks with live results

Install dependencies with `./run.sh` or a virtualenv plus
`pip install -r requirements.txt`. Live verification loads root/backend `.env`
without overriding exported environment variables:

```bash
# Session login + account quota only. NO award search; does rotate the session:
.venv/bin/python backend/tools/verify_flybasis_socket.py --live --auth-only

# One real award search; defaults to JFK–LHR business, today + 30 days:
.venv/bin/python backend/tools/verify_flybasis_socket.py --live
# Optional: --origin SFO --destination NRT --date YYYY-MM-DD --cabin business
```

`--auth-only` does not verify socket access. The live search sends **one** query
and reports the actual result count, not the mock's two specific itineraries.
Zero results are explicitly reported as no availability, not invented flights.
A failed login, unknown account status, socket error or timeout is not a pass.

Common failures:

- **Disabled provider:** both the app key and a session credential are required,
  or use an official socket key. Local `.env` changes require an app restart;
  Vercel changes require a redeploy.
- **Session rejected:** replace revoked/rotated credentials privately. Do not
  repeatedly replay the stale request token from a HAR.
- **Zero quota:** a valid login does not grant more searches.
- **TLS/network error:** connectivity must work before credentials can be
  validated. A connection failure is not evidence that a token is invalid.
- **Empty live search:** try another valid route/date on the provider's site;
  never substitute modeled flights or web-search snippets as availability.

## Offline regression gates

```bash
.venv/bin/python -m unittest discover -s backend/tests -p 'test_*.py' -v
(cd backend && ../.venv/bin/python tests_integration.py)
.venv/bin/python backend/tools/verify_flybasis_socket.py
(cd frontend/test && npm ci && npm test)
```

The socket verifier's default mode starts local HTTP/WebSocket mocks with
synthetic credentials, single-use refresh rotation and the captured account
header/body shape. It proves the client contract, **not production access**.
The workflow template is `ci/live-check.workflow.yml`; it runs on GitHub only
after the repository owner installs it under `.github/workflows/`.
