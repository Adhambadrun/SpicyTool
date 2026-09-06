#!/usr/bin/env bash
# Verify the AgentSearch integration against the REAL RapidAPI endpoint.
#
# Run this on any machine with normal outbound internet access:
#
#   AGENTSEARCH_API_KEY=<your RapidAPI key> ./run_live_check.sh
#
# It (1) reproduces the raw curl so you can see the upstream answer, then
# (2) runs the five integration assertions through the actual app code.
set -u
cd "$(dirname "$0")"

KEY="${AGENTSEARCH_API_KEY:-${FLYBASIS_API_KEY:-}}"
if [ -z "$KEY" ]; then
  echo "Set AGENTSEARCH_API_KEY to your RapidAPI key first:"
  echo "  AGENTSEARCH_API_KEY=<key> ./run_live_check.sh"
  exit 2
fi

if [ ! -x .venv/bin/python ]; then
  echo ">> creating virtualenv"
  python3 -m venv .venv
  .venv/bin/pip install --quiet -r backend/requirements.txt
fi

echo "=============================================================="
echo " 1. Raw upstream call (same shape as the RapidAPI docs snippet)"
echo "=============================================================="
curl -sS --max-time 30 --request GET \
  --url 'https://agentsearch.p.rapidapi.com/v1/search?provider=brave&country=us&limit=3&query=anthropic%20claude' \
  --header 'Content-Type: application/json' \
  --header 'x-rapidapi-host: agentsearch.p.rapidapi.com' \
  --header "x-rapidapi-key: ${KEY}" \
  | .venv/bin/python -m json.tool || echo "(raw curl failed — see the error above)"

echo
echo "=============================================================="
echo " 2. Integration assertions through the real app code"
echo "=============================================================="
cd backend
AGENTSEARCH_API_KEY="$KEY" ../.venv/bin/python tools/verify_agentsearch.py --live
rc=$?

echo
if [ $rc -eq 0 ]; then
  echo "LIVE CHECK PASSED — the key works and the app is wired to it."
  echo "Start the app with:  AGENTSEARCH_API_KEY=<key> ./run.sh"
else
  echo "LIVE CHECK FAILED (exit $rc). Most likely causes:"
  echo "  * the RapidAPI account is not subscribed to the AgentSearch API (401/403)"
  echo "  * the monthly quota is exhausted (429)"
  echo "  * this machine cannot reach agentsearch.p.rapidapi.com"
fi
exit $rc
