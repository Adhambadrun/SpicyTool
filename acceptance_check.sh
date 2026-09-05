#!/usr/bin/env bash
# SpicyTool §16 acceptance sweep against a running server on :8000
set -u
cd "$(dirname "$0")"
PY=.venv/bin/python
pass=0; fail=0
ok()   { echo -e "\033[92mPASS\033[0m  $1"; pass=$((pass+1)); }
bad()  { echo -e "\033[91mFAIL\033[0m  $1"; fail=$((fail+1)); }

echo "== 1. tests_integration.py: 16/16 =="
if (cd backend && ../.venv/bin/python tests_integration.py | grep -q "All 16 assertions passed"); then ok "16/16 assertions"; else bad "integration tests"; fi

echo "== 2. /api/v1/health =="
h=$(curl -s localhost:8000/api/v1/health)
[ "$(echo $h | $PY -c 'import json,sys;d=json.load(sys.stdin);print(d["airports"]==78 and d["programs"]==14)')" = "True" ] && ok "78 airports, 14 programs" || bad "$h"

echo "== 3. JFK->LHR distance =="
d=$(cd backend && ../.venv/bin/python -c "from core.geo import haversine_miles as h; print(round(h('JFK','LHR')))")
[ "$d" = "3442" ] && ok "3,442 mi" || bad "got $d"

echo "== 4. JFK->LHR business raw vs deduped (v2) =="
curl -s 'localhost:8000/api/v2/search?origin=JFK&destination=LHR&date=2026-09-16&cabin=business' > /tmp/v2.json
$PY - <<'EOF'
import json
d = json.load(open('/tmp/v2.json'))
raw, merged = d['dedupe']['raw_count'], d['dedupe']['merged_count']
assert 15 <= raw <= 40, f"raw {raw} not ~25"
assert 8 <= merged <= 16, f"merged {merged} not ~12"
print(f"raw={raw} -> merged={merged} (collapsed {d['dedupe']['duplicates_collapsed']})")
EOF
[ $? -eq 0 ] && ok "raw ~25 -> ~12 after dedupe" || bad "counts out of band"

echo "== 5. some programs legitimately return 0 =="
z=$(curl -s 'localhost:8000/api/v1/search?origin=JFK&destination=LHR&date=2026-09-16&cabin=business' | $PY -c "
import json,sys,collections
d=json.load(sys.stdin)
c=collections.Counter(r['pricing']['program_code'] for r in d['results'])
zeros=[p for p in ['AC_AEROPLAN','UA_MILEAGEPLUS','AV_LIFEMILES','TK_MILESSMILES','SQ_KRISFLYER','ET_SHEBAMILES','AF_FLYINGBLUE','DL_SKYMILES','VS_FLYINGCLUB','BA_AVIOS','QR_PRIVILEGECLUB','AA_AADVANTAGE','AS_MILEAGEPLAN','EK_SKYWARDS'] if c[p]==0]
print(len(zeros)>0)")
[ "$z" = "True" ] && ok "zero-result programs exist on this route" || bad "none"

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
for name in ('AwardTool', 'PointsPath', 'PointsYeah'):
    assert p[name]['enabled'] is False and p[name]['disabled_reason']
print("SpicyToolEngine on; 3 gated with reasons")
EOF
[ $? -eq 0 ] && ok "gating correct" || bad "gating"

echo "== 8. disabled providers ok:false without failing request =="
$PY - <<'EOF'
import json
d = json.load(open('/tmp/v2.json'))
sts = {p['provider']: p for p in d['providers']}
assert all(sts[n]['ok'] is False and 'env' in (sts[n]['error'] or '').lower() or 'check' in (sts[n]['error'] or '').lower() or 'set the' in (sts[n]['error'] or '').lower() for n in ('AwardTool','PointsPath','PointsYeah'))
assert d['count'] > 0, "request itself failed"
print("3x ok:false + actionable reason; aggregate still returned", d['count'], "results")
EOF
[ $? -eq 0 ] && ok "failure isolation" || bad "isolation"

echo "== 9. SSE start -> data per provider -> complete =="
ev=$(timeout 10 curl -sN 'localhost:8000/api/v2/search/stream?origin=LAX&destination=NRT&date=2026-12-01&cabin=business' | grep -a '^event:' | sed 's/event: //;s/\r//' | tr '\n' ' ' | sed 's/ *$//')
echo "  events: $ev"
case "$ev" in
  "start data data data data complete") ok "v2 SSE sequence" ;;
  *) bad "v2 SSE sequence: $ev" ;;
esac

echo "== 10. deterministic / date-sensitive =="
r1=$(curl -s 'localhost:8000/api/v1/search?origin=CAI&destination=JFK&date=2026-09-20&cabin=economy')
r1b=$(curl -s 'localhost:8000/api/v1/search?origin=CAI&destination=JFK&date=2026-09-20&cabin=economy')
r2=$(curl -s 'localhost:8000/api/v1/search?origin=CAI&destination=JFK&date=2026-09-21&cabin=economy')
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

echo "== 13. frontend served same-origin =="
c=$(curl -s -o /dev/null -w '%{http_code}' localhost:8000/)
[ "$c" = "200" ] && ok "GET / -> 200 index.html" || bad "status $c"

echo "== 14. multi-airport search (up to 3 per side) =="
$PY - <<'EOF'
import json, urllib.request, urllib.error
def get(path):
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
$PY - <<'EOF'
import json, urllib.request, urllib.error
def get(path):
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

echo
echo "=============================="
echo -e "ACCEPTANCE: \033[92m$pass passed\033[0m, \033[91m$fail failed\033[0m"
exit $fail
