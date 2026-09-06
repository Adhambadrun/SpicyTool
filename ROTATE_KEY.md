# Rotating exposed credentials

Two separate exposures were found in this public repo. **Rotate both — today.**

---

## A. Session credentials from `agentsearch.vercel.app.har`  ⚠️ MOST URGENT

`agentsearch.vercel.app.har` was uploaded to this **public** repo. It contains
a live web session for a real Flybasis user account
(`solomon@lux-flights.com`, id `59e79ed3-…`):

- a **Supabase refresh_token** and **access_token** for
  `sb.flybasis.com` (project ref `zdghaeihevurffircfun`),
- the account's permission flags (`canHC`, `canMax`) and
  `maxSearchesRemaining: 5` from `api2.flybasis.com/trpc/user.whoami`.

Anyone who has seen the repo can mint live sessions as that account and spend
its remaining searches. The file is removed from the branch and `*.har` is
now gitignored, but it remains in `main`'s history (`f8e93ad`) — deletion does
**not** un-leak it.

**Rotate now (no code change needed):**

1. Change the password of the `solomon@lux-flights.com` Flybasis account
   (Flybasis account settings). Supabase refresh tokens die when the password
   changes — this kills the leaked session.
2. If that account belongs to Lux Flights and is shared, coordinate with them:
   the token grants access to whatever that account can see.
3. Watch Flybasis/agency usage for the coming weeks; if the session was
   already used, ask Flybasis `support@flybasis.com` to review the account's
   search history.
4. Optionally ask GitHub Support to purge the file from history
   (Support → "Sensitive data"), since the repo is public.

**Never use the HAR tokens in SpicyTool** — they belong to a third-party
account, and using them would (a) be unauthorized credential use and (b) burn
that account's quota. There is a safe session path documented in
`FLYBASIS_GO_LIVE.md` § Option B that uses *your own* credentials.

---

## B. The exposed RapidAPI key

The key `ebd27a2097msh…4156` was committed to this **public** repo in commit
`bbd85a2` (as a test fixture in `backend/tests_integration.py`). The file is
fixed, but the value is still in that commit's history and must be treated as
compromised.

Rotating is quick, and RapidAPI is designed for exactly this: you **add** a new
key first, switch over, then **delete** the old one — so nothing breaks in
between and your app's analytics are preserved.

---

## Step 1 — Create the new key (~1 minute)

1. Go to the [RapidAPI Developer Dashboard](https://rapidapi.com/developer/apps).
2. Select the app that holds the compromised key (there's a default app if you
   never created others).
3. Open the **Authorization** tab.
4. Click **Add Authorization**, give it a name (e.g. `spicytool-2026-09`), and
   save.
5. Copy the new `X-RapidAPI-Key` value.

> Adding a second authorization does **not** disturb the old one yet — both work
> until you delete the old, which is what makes this a zero-downtime swap.

## Step 2 — Verify the new key works

On the AgentSearch listing's **Endpoints** tab, pick the new key from the
`X-RapidAPI-Key` dropdown and hit **Test Endpoint**. Expect HTTP 200.

Or from anywhere with internet access:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' \
  --url 'https://agentsearch.p.rapidapi.com/v1/search?provider=brave&country=us&limit=3&query=test' \
  --header 'x-rapidapi-host: agentsearch.p.rapidapi.com' \
  --header 'x-rapidapi-key: NEW_KEY_HERE'
# expect: 200
```

## Step 3 — Put the new key where the app reads it

Pick the places you actually deploy to:

| Where | How |
|---|---|
| **GitHub Actions** | Settings → Secrets and variables → Actions → `AGENTSEARCH_API_KEY` |
| **Vercel** | Project → Settings → Environment Variables → `AGENTSEARCH_API_KEY` (then redeploy) |
| **Local** | `printf 'AGENTSEARCH_API_KEY=NEW_KEY\n' >> .env` (`.env` is git-ignored) |

Never put it in a tracked file. `.env.example` documents the variable with a
blank value on purpose.

## Step 4 — Delete the compromised key

Back on the app's **Authorization** page, delete the old authorization
(`ebd27a2097msh…4156`). From that moment the leaked value is worthless, which
is the whole point — it makes the exposure in git history harmless.

## Step 5 — Confirm

```bash
AGENTSEARCH_API_KEY=<new key> ./run_live_check.sh
```

Or push and let the CI workflow run it (see `.github/workflows/live-check.yml`).

---

## Do I need to scrub git history?

**No — and I'd recommend against it here.** Once Step 4 is done the old key is
revoked and worthless, so rewriting history buys nothing. A rewrite
(`git filter-repo` / BFG) force-pushes every commit, breaks anyone's existing
clones and open PRs, and the old objects usually survive in GitHub's cache
anyway. Revocation is the real fix; history scrubbing is cosmetic.

## Worth checking while you're there

- **Usage graph** — Developer Dashboard → your app → *Analytics*. A spike you
  don't recognise between the leak and the rotation would mean someone used it.
  Given the short window this is unlikely, but it's a 10-second look.
- **Quota** — if the key was abused, your monthly quota may have been consumed.

## Avoiding a repeat

- Keep credentials in `.env` (git-ignored) or a platform secret store, never in
  a tracked file — including test fixtures. The suite now uses a synthetic
  RapidAPI-shaped value (`0123456789msh…`) instead.
- Never upload browser captures: a HAR records every token your session uses
  (`*.har` is gitignored here; inspect captures with a text editor or the
  browser before sharing anywhere, and scrub auth headers/payloads).
- Enable **GitHub secret scanning + push protection** on the repo
  (Settings → Code security). It blocks commits containing recognised
  credential formats before they land.
- Use a separate RapidAPI app per project so one leak never forces you to
  rotate everything at once.
