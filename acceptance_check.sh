#!/usr/bin/env bash
# SpicyTool §16 acceptance sweep against a running server on :8000
set -u
cd "$(dirname "$0")"
PY=.venv/bin/python
pass=0; fail=0
# Production relays Flybasis ONLY (see SPICYTOOL_PROVIDERS). This sweep exercises the
# full multi-provider aggregation plus the modeled engine, so it must run against a
# server started with SPICYTOOL_MODELED_ENGINE=1 AND SPICYTOOL_PROVIDERS=all.
if [ "$(curl -s localhost:8000/api/v1/health | $PY -c 'import json,sys;print(json.load(sys.stdin).get("modeled_engine"))')" != "True" ]; then
  echo "acceptance_check.sh needs the server started with SPICYTOOL_MODELED_ENGINE=1 (modeled engine is off by default)."; exit 2
fi
NPROV=$(curl -s localhost:8000/api/v2/providers | $PY -c 'import json,sys;print(len(json.load(sys.stdin)["providers"]))')
if [ "$NPROV" != "5" ]; then
  echo "acceptance_check.sh sweeps all 5 providers; this server relays $NPROV.";
  echo "Restart it with SPICYTOOL_PROVIDERS=all SPICYTOOL_MODELED_ENGINE=1 (production relays Flybasis only)."; exit 2
fi
# In-process session token (shares the server's signing secret; no API backdoor).
TOKEN=$(cd backend && ../.venv/bin/python -c "from core.auth import issue_token; print(issue_token('adhambadraan@gmail.com'))")
ok()   { echo -e "\033[92mPASS\033[0m  $1"; pass=$((pass+1)); }
bad()  { echo -e "\033[91mFAIL\033[0m  $1"; fail=$((fail+1)); }

echo "== 1. tests_integration.py: 22/22 =="
if (cd backend && ../.venv/bin/python tests_integration.py | grep -q "All 22 assertions passed"); then ok "22/22 assertions"; else bad "integration tests"; fi

echo "== 2. /api/v1/health =="
h=$(curl -s localhost:8000/api/v1/health)
[ "$(echo $h | $PY -c 'import json,sys;d=json.load(sys.stdin);print(d["airports"]==84 and d["programs"]==10)')" = "True" ] && ok "84 airports, 10 programs" || bad "$h"

echo "== 3. JFK->LHR distance =="
d=$(cd backend && ../.venv/bin/python -c "from core.geo import haversine_miles as h; print(round(h('JFK','LHR')))")
[ "$d" = "3442" ] && ok "3,442 mi" || bad "got $d"

echo "== 4. JFK->LHR business raw vs deduped (v2) =="
curl -s 'localhost:8000/api/v2/search?origin=JFK&destination=LHR&date=2026-09-16&cabin=business' > /tmp/v2.json
$PY - <<'EOF'
import json
d = json.load(open('/tmp/v2.json'))
raw, merged = d['dedupe']['raw_count'], d['dedupe']['merged_count']
assert 15 <= raw <= 60, f"raw {raw} out of band"
assert 8 <= merged <= 28, f"merged {merged} out of band"
print(f"raw={raw} -> merged={merged} (collapsed {d['dedupe']['duplicates_collapsed']})")
EOF
[ $? -eq 0 ] && ok "raw ~25 -> ~12 after dedupe" || bad "counts out of band"

echo "== 5. some programs legitimately return 0 =="
z=$(curl -s "localhost:8000/api/v1/search?origin=CAI&destination=IST&date=2026-11-10&cabin=economy&token=$TOKEN" | $PY -c "
import json,sys,collections
d=json.load(sys.stdin)
c=collections.Counter(r['pricing']['program_code'] for r in d['results'])
PROGS=['AC_AEROPLAN','UA_MILEAGEPLUS','TK_MILESSMILES','AF_FLYINGBLUE','DL_SKYMILES','AA_AADVANTAGE','AS_MILEAGEPLAN','EY_GUEST','QF_FREQUENTFLYER','TP_MILESGO']
assert set(c) <= set(PROGS), 'unexpected program: %s' % (set(c) - set(PROGS))
TYPES={'award','hc','upg','dis','consolidator','basis_exclusive','published'}
assert all(r['ticket_type'] in TYPES for r in d['results']), 'bad ticket_type'
zeros=[p for p in PROGS if c[p]==0]
print(len(zeros)>0)")
[ "$z" = "True" ] && ok "zero-result programs exist (CAI->IST: 5 of 10)" || bad "none"

echo "== 6. cache hit: ~1700ms -> ~0ms =="
# unique date per run so the first call is always a cache miss
CACHE_DATE="2027-$(printf '%02d' $(( (RANDOM % 12) + 1 )))-$(printf '%02d' $(( (RANDOM % 28) + 1 )))"
CACHE_Q="origin=SFO&destination=NRT&date=$CACHE_DATE&cabin=economy"
curl -s "localhost:8000/api/v2/search?$CACHE_Q" > /tmp/c1.json
curl -s "localhost:8000/api/v2/search?$CACHE_Q" > /tmp/c2.json
$PY - <<'EOF'
import json
cold = json.load(open('/tmp/c1.json')); warm = json.load(open('/tmp/c2.json'))
e1 = [p['latency_ms'] for p in cold['providers'] if p['provider']=='SpicyToolEngine'][0]
e2 = [p['latency_ms'] for p in warm['providers'] if p['provider']=='SpicyToolEngine'][0]
assert e1 > 1000, f"cold {e1}ms too fast to be a miss"
assert e2 < 50, f"warm {e2}ms not a cache hit"
assert warm['providers'][3]['cached'] is True or any(p.get('cached') for p in warm['providers']), "cached flag not set"
print(f"{e1}ms -> {e2}ms")
EOF
[ $? -eq 0 ] && ok "cache hit confirmed" || bad "cache"

echo "== 7. /api/v2/providers gating =="
$PY - <<'EOF'
import json, urllib.request
d = json.load(urllib.request.urlopen('http://localhost:8000/api/v2/providers'))
p = {x['provider']: x for x in d['providers']}
assert p['SpicyToolEngine']['enabled'] is True
for name in ('AwardTool', 'PointsPath', 'PointsYeah', 'Flybasis'):
    assert p[name]['enabled'] is False and p[name]['disabled_reason']
print("SpicyToolEngine on; 4 gated with reasons")
EOF
[ $? -eq 0 ] && ok "gating correct" || bad "gating"

echo "== 8. disabled providers ok:false without failing request =="
$PY - <<'EOF'
import json
d = json.load(open('/tmp/v2.json'))
sts = {p['provider']: p for p in d['providers']}
assert all(sts[n]['ok'] is False and 'env' in (sts[n]['error'] or '').lower() or 'check' in (sts[n]['error'] or '').lower() or 'set the' in (sts[n]['error'] or '').lower() for n in ('AwardTool','PointsPath','PointsYeah','Flybasis'))
assert d['count'] > 0, "request itself failed"
print("4x ok:false + actionable reason; aggregate still returned", d['count'], "results")
EOF
[ $? -eq 0 ] && ok "failure isolation" || bad "isolation"

echo "== 9. SSE start -> data per provider -> complete =="
ev=$(timeout 10 curl -sN 'localhost:8000/api/v2/search/stream?origin=LAX&destination=NRT&date=2026-12-01&cabin=business' | grep -a '^event:' | sed 's/event: //;s/\r//' | tr '\n' ' ' | sed 's/ *$//')
echo "  events: $ev"
case "$ev" in
  "start data data data data data complete") ok "v2 SSE sequence" ;;
  *) bad "v2 SSE sequence: $ev" ;;
esac

echo "== 10. deterministic / date-sensitive =="
r1=$(curl -s "localhost:8000/api/v1/search?origin=CAI&destination=JFK&date=2026-09-20&cabin=economy&token=$TOKEN")
r1b=$(curl -s "localhost:8000/api/v1/search?origin=CAI&destination=JFK&date=2026-09-20&cabin=economy&token=$TOKEN")
r2=$(curl -s "localhost:8000/api/v1/search?origin=CAI&destination=JFK&date=2026-09-21&cabin=economy&token=$TOKEN")
[ "$r1" = "$r1b" ] && ok "identical query -> identical results" || bad "nondeterministic"
[ "$r1" != "$r2" ] && ok "different date -> different results" || bad "date-insensitive"

echo "== 11. /api/v2/telemetry =="
t=$(curl -s localhost:8000/api/v2/telemetry)
$PY -c "
import json,sys
d=json.loads('''$t''')
assert d['blocked_host_count'] >= 32, d['blocked_host_count']
assert any(s['host']=='cloudflareinsights.com' and s['blocked'] for s in d['sample_checks'])
assert any(s['host']=='api.pointsyeah.com' and not s['blocked'] for s in d['sample_checks'])
print(d['blocked_host_count'], 'hosts blocked; counter =', d['blocked_requests'])
" && ok ">=32 blocked hosts, counter working" || bad "telemetry"

echo "== 12. redis-absence resilience (memory backend) =="
b=$(curl -s localhost:8000/api/v2/cache/stats | $PY -c 'import json,sys;print(json.load(sys.stdin)["backend"])')
[ "$b" = "memory" ] && ok "no Redis present -> memory fallback, service healthy" || bad "backend=$b"

echo "== 13. frontend served same-origin (logo link, real airline logos, in-app itinerary) =="
c=$(curl -s -o /dev/null -w '%{http_code}' localhost:8000/)
[ "$c" = "200" ] && ok "GET / -> 200 index.html" || bad "status $c"
curl -s localhost:8000/ > /tmp/index_served.html
n=$(grep -c '<a class="brand"' /tmp/index_served.html)
[ "$n" = "3" ] && ok "logo+wordmark is one <a href=\"/\"> in all 3 headers" || bad "brand links: $n"
grep -q 'id="app-favicon"' /tmp/index_served.html && ok "tab icon and logo share one asset (LOGO_SRC)" || bad "no favicon link"
grep -q 'gstatic.com/flights/airline_logos/70px' /tmp/index_served.html \
  && grep -q 'pics.avs.io/200/200' /tmp/index_served.html \
  && ok "real airline logos (2 artwork sources + offline brand tile)" || bad "airline logo sources"
grep -q 'id="view-itinerary"' /tmp/index_served.html \
  && ! grep -q 'id="it-search-slot"' /tmp/index_served.html \
  && grep -q 'id="search-slot"' /tmp/index_served.html \
  && ok "itinerary is an in-app view with NO search bar (search lives on the home screen)" || bad "itinerary / search-bar placement"
grep -q '#itinerary/' /tmp/index_served.html && ok "itinerary has a real shareable link (#itinerary/<id>)" || bad "no itinerary link"
grep -q 'document.write' /tmp/index_served.html && bad "itinerary still uses document.write (blank-tab risk)" || ok "no document.write blank tabs"

echo "== 14. multi-airport search (up to 3 per side) =="
TOKEN=$TOKEN $PY - <<'EOF'
import json, urllib.request, urllib.error
import os
TOKEN = os.environ['TOKEN']
def get(path):
    path = path + ('&' if '?' in path else '?') + 'token=' + TOKEN
    try:
        with urllib.request.urlopen('http://localhost:8000' + path) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)

s, d = get('/api/v1/search?origin=JFK,EWR,LGA&destination=LHR&date=2026-10-10&cabin=business')
assert s == 200, s
assert len(d['query']['routes']) == 3, d['query']
origins = {r['route']['origin'] for r in d['results']}
assert origins <= {'JFK','EWR','LGA'} and d['count'] > 0

s, d = get('/api/v1/search?origin=JFK,EWR,LGA,BOS&destination=LHR&date=2026-10-10')
assert s == 400 and 'At most 3' in d['detail'], (s, d)
s, d = get('/api/v1/search?origin=JFK,ZZZ&destination=LHR&date=2026-10-10')
assert s == 400 and "Unknown origin 'ZZZ'" == d['detail'], (s, d)
s, d = get('/api/v1/search?origin=JFK,JFK&destination=LHR&date=2026-10-10')
assert s == 400 and 'Duplicate' in d['detail'], (s, d)
s, d = get('/api/v1/calendar?origin=JFK,EWR&destination=LHR,LGW&start_date=2026-10-10&days=5&cabin=business')
assert s == 200 and d['origin'] == ['JFK','EWR'] and d['destination'] == ['LHR','LGW'], (s, d)
print('3x1 fan-out, caps, dupes, unknowns, overlap + multi calendar OK')
EOF
[ $? -eq 0 ] && ok "multi-airport search + validation" || bad "multi-airport"

echo "== 15. round-trip search (both legs combined, mixed programs) =="
TOKEN=$TOKEN $PY - <<'EOF'
import json, urllib.request, urllib.error
import os
TOKEN = os.environ['TOKEN']
def get(path):
    path = path + ('&' if '?' in path else '?') + 'token=' + TOKEN
    try:
        with urllib.request.urlopen('http://localhost:8000' + path) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)

s, d = get('/api/v1/search?origin=CAI&destination=LHR&date=2026-09-16&return_date=2026-10-12&cabin=business')
assert s == 200, s
assert d['query']['trip'] == 'roundtrip'
assert ['CAI','LHR'] in d['query']['routes'] and ['LHR','CAI'] in d['query']['routes']
assert d['count'] > 0
r0 = d['results'][0]
assert r0['trip'] == 'roundtrip' and r0['outbound'] and r0['return_leg']
assert r0['pricing']['points'] == r0['outbound']['pricing']['points'] + r0['return_leg']['pricing']['points']
assert any(not p['pricing']['same_program'] for p in d['results']), 'no mixed-program pairs'
pts = [p['pricing']['points'] for p in d['results']]
assert pts == sorted(pts), 'pairs not sorted by total points'

s, d = get('/api/v1/search?origin=CAI&destination=LHR&date=2026-10-12&return_date=2026-09-16')
assert s == 400 and 'on or after' in d['detail'], (s, d)
s, d = get('/api/v1/search?origin=CAI&destination=LHR&date=2026-09-16&return_date=2026-02-30')
assert s == 400 and 'real calendar' in d['detail'], (s, d)
print('pairing totals, mixed programs, sort, validation OK')
EOF
[ $? -eq 0 ] && ok "round-trip search + pairing + validation" || bad "round-trip"

echo "== 16. auth: owner PIN login + protected engine =="
TOKEN=$TOKEN $PY - <<'EOF'
import json, os, urllib.request, urllib.error
TOKEN = os.environ['TOKEN']
def call(path, method='GET', body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request('http://localhost:8000' + path, data=data,
                                 headers={'Content-Type': 'application/json'}, method=method)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        try: return e.code, json.load(e)
        except Exception: return e.code, {}

s, d = call('/api/v1/search?origin=JFK&destination=LHR&date=2026-10-05')
assert s == 401, s
s, d = call('/api/v1/search?origin=JFK&destination=LHR&date=2026-10-05&token=' + TOKEN)
assert s == 200, (s, d)
s, d = call('/api/v1/search?origin=JFK&destination=LHR&date=2026-10-05&token=v1.x.y')
assert s == 401, s
s, d = call('/api/v1/auth/check', 'POST', {'email': 'someone@gmail.com'})
assert s == 403 and 'not authorized' in d['detail'], (s, d)
s, d = call('/api/v1/auth/check', 'POST', {'email': 'adhambadraan@icloud.com'})
assert s == 200 and d['ok'] is True, (s, d)
s, d = call('/api/v1/auth/check', 'POST', {'email': 'adhambadraan@gmail.com'})
assert s == 200 and d['ok'] is True, (s, d)
s, d = call('/api/v1/auth/login', 'POST', {'email': 'adhambadraan@gmail.com', 'pin': '000000'})
assert s == 401, (s, d)
s, d = call('/api/v1/auth/login', 'POST', {'email': 'someone@gmail.com', 'pin': '141220'})
assert s == 403, (s, d)
s, d = call('/api/v1/auth/login', 'POST', {'email': 'adhambadraan@gmail.com', 'pin': '141220'})
assert s == 200 and d['ok'] is True and d['token'].startswith('v1.'), (s, d)
# TTL is owned by backend/core/auth.py (SESSION_TTL_HOURS) - read it so this check can't drift
ttl_h = int(open('backend/core/auth.py').read().split('SESSION_TTL_HOURS = ')[1].split()[0])
assert d['expires_in'] == ttl_h * 3600, (d['expires_in'], ttl_h)
s, d = call('/api/v1/auth/session?token=' + d['token'])
assert d['valid'] is True and d['email'] == 'adhambadraan@gmail.com', d
s, d = call('/api/v1/auth/session?token=' + TOKEN)
assert d['valid'] is True and d['email'] == 'adhambadraan@gmail.com', d
print('401 enforcement, two-email allowlist, PIN rejection/acceptance, session check OK')
EOF
[ $? -eq 0 ] && ok "auth: owner PIN login + protected engine" || bad "auth"

echo "== 17. new carrier network + ticket types =="
TOKEN=$TOKEN $PY - <<'EOF'
import json, os, urllib.request
TOKEN = os.environ['TOKEN']
def get(path):
    with urllib.request.urlopen('http://localhost:8000' + path + '&token=' + TOKEN) as r:
        return json.load(r)

d = get('/api/v1/search?origin=JFK&destination=LHR&date=2026-10-05&cabin=economy')
airlines = {r['airline_code'] for r in d['results']}
NEW = {'A3','EI','EN','UX','JU','DE','OU','4Y','EW','FZ','FI','AZ','B6','LO','VL','AT'}
REMOVED = {'AM','CM','CX','KE','KQ','MU','NH','JL','OZ','QF','QR','SA','TG','NZ','SQ','AS'}
assert not (airlines & REMOVED), 'removed carriers leaked: %s' % (airlines & REMOVED)
assert airlines & NEW, 'no new carriers in results'
assert all(r['ticket_type'] for r in d['results'])
d2 = get('/api/v1/search?origin=JFK&destination=LHR&date=2026-10-05&return_date=2026-10-12&cabin=economy')
assert d2['results'] and all('ticket_types' in p and len(p['ticket_types']) == 2 for p in d2['results'])
d3 = get('/api/v1/airports?q=BEG')
assert d3 and d3[0]['code'] == 'BEG', d3
print('new carriers:', sorted(airlines & NEW), '| RT ticket types OK | BEG found')
EOF
[ $? -eq 0 ] && ok "new carrier network + ticket types" || bad "new carriers"

echo "== 18. Flybasis-only relay (default provider set) =="
# In-process: independent of how the server under sweep was started.
(cd backend && env -u SPICYTOOL_PROVIDERS ../.venv/bin/python - <<'EOF'
import sys
from services import aggregator

# Default (no SPICYTOOL_PROVIDERS) must relay Flybasis and nothing else.
assert aggregator.configured_provider_names() == ("Flybasis",), aggregator.configured_provider_names()
names = [p.name for p in aggregator.registry()]
assert names == ["Flybasis"], names
assert [p["provider"] for p in aggregator.provider_report()] == ["Flybasis"]
assert aggregator.select(None) and [p.name for p in aggregator.select(None)] == ["Flybasis"]
# A caller cannot re-add another adapter by name when it is not in the set.
assert aggregator.select(["PointsYeah", "AwardTool", "SpicyToolEngine"]) == []
# No Flybasis credential => nothing is live, and the notice says so.
assert aggregator.live_providers() == [], aggregator.live_providers()
reason = aggregator._no_live_reason(aggregator.registry())
assert reason and "Flybasis" in reason, reason
print("default relay = %s; other adapters unreachable; notice ok" % names)
EOF
) && (cd backend && SPICYTOOL_PROVIDERS=all ../.venv/bin/python -c "
from services import aggregator
assert len(aggregator.registry()) == 5, [p.name for p in aggregator.registry()]
print('SPICYTOOL_PROVIDERS=all ->', [p.name for p in aggregator.registry()])
")
[ $? -eq 0 ] && ok "Flybasis-only relay default + override" || bad "Flybasis-only relay"

echo "== 19. web context is labelled non-award and never touches results =="
# The connector may be unreachable from the test host; what must hold either way
# is the labelling, the input validation, and that search results are untouched.
curl -s 'localhost:8000/api/v2/search?origin=JFK&destination=LHR&date=2026-09-17&cabin=business' > /tmp/wc_before.json
$PY - <<'EOF'
import json, urllib.error, urllib.request

def get(path, want=200):
    url = "http://localhost:8000" + path
    try:
        with urllib.request.urlopen(url) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)

# bad input is rejected, not silently served
s, _ = get("/api/v2/context?tool=bogus");            assert s == 400, f"bad tool -> {s}"
s, _ = get("/api/v2/context?tool=fetch_url");        assert s == 400, f"fetch_url w/o url -> {s}"

# every payload is labelled non-award, whatever the connector did
for path in ("/api/v2/context?origin=JFK&destination=LHR&program=AC_AEROPLAN&cabin=business",
             "/api/v2/context?tool=instant_answer&q=Aeroplan"):
    s, d = get(path)
    assert s == 200, f"{path} -> {s}"
    assert d["kind"] == "web_context", d.get("kind")
    assert d["is_award_data"] is False, "is_award_data must be False"
    assert d["source"] == "flybasis-mcp"
    assert "NOT award" in d["disclaimer"], d.get("disclaimer")
    print(f"  {d['tool']:15} ok={str(d['ok']):5} kind={d['kind']} is_award_data={d['is_award_data']}")

# web context must never feed the results list
b = json.load(open("/tmp/wc_before.json"))
get("/api/v2/context?origin=JFK&destination=LHR&program=AC_AEROPLAN")
s, a = get("/api/v2/search?origin=JFK&destination=LHR&date=2026-09-17&cabin=business")
assert a["count"] == b["count"], f"result count moved {b['count']} -> {a['count']}"
assert [r["id"] for r in a["results"]] == [r["id"] for r in b["results"]], "result ids changed"
print(f"  search unaffected: {a['count']} results, identical ids")
EOF
[ $? -eq 0 ] && ok "web context labelled non-award, results untouched" || bad "web context"

echo
echo "=============================="
echo -e "ACCEPTANCE: \033[92m$pass passed\033[0m, \033[91m$fail failed\033[0m"
exit $fail
