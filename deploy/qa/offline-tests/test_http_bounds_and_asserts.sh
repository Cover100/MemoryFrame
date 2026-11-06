#!/bin/bash
# Stub HTTP server: prove curl timeouts + success-rate assertions logic.
set -eu
WORKDIR=$(mktemp -d)
trap 'kill $(jobs -p) 2>/dev/null || true; rm -rf "$WORKDIR"' EXIT

python3 - <<'PY' &
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json, time
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path.startswith('/hang'):
            time.sleep(30)
            self.send_response(200); self.end_headers(); return
        if self.path.startswith('/admin') or self.path.startswith('/api/v1/status'):
            self.send_response(200); self.end_headers(); self.wfile.write(b'ok'); return
        if self.path.startswith('/brightness'):
            self.send_response(200); self.end_headers()
            self.wfile.write(json.dumps({"minimum":40,"maximum":255}).encode()); return
        self.send_response(404); self.end_headers()
    def do_POST(self):
        n=int(self.headers.get('Content-Length',0)); self.rfile.read(n)
        if self.path.startswith('/brightness'):
            self.send_response(200); self.end_headers(); self.wfile.write(b'{}'); return
        self.send_response(404); self.end_headers()
ThreadingHTTPServer(('127.0.0.1', 8765), H).serve_forever()
PY
for i in 1 2 3 4 5 6 7 8 9 10; do
  if curl -sS --connect-timeout 1 --max-time 1 -o /dev/null http://127.0.0.1:8765/admin/ 2>/dev/null; then break; fi
  sleep 0.2
done

PASS=0; FAIL=0
ok(){ echo "PASS: $*"; PASS=$((PASS+1)); }
bad(){ echo "FAIL: $*"; FAIL=$((FAIL+1)); }

http_code() {
  # curl still prints %{http_code} on timeout (often 000); do not append via ||
  local c
  c=$(curl -sS --connect-timeout 2 --max-time 2 -o /dev/null -w "%{http_code}" "$@" 2>/dev/null || true)
  [ -z "$c" ] && c=000
  printf '%s' "$c"
}

start=$(date +%s)
code=$(http_code http://127.0.0.1:8765/hang)
elapsed=$(( $(date +%s) - start ))
if [ "$elapsed" -le 5 ] && [ "$code" != "200" ]; then
  ok "curl max-time bound hung endpoint (${elapsed}s code=$code)"
else
  bad "curl did not bound hang elapsed=$elapsed code=$code"
fi

ADMIN_OK=0; BR_OK=0
for i in 1 2 3 4 5; do
  AC=$(http_code http://127.0.0.1:8765/admin/)
  [ "$AC" = 200 ] && ADMIN_OK=$((ADMIN_OK+1))
  BC=$(http_code -X POST -H 'Content-Type: application/json' -d '{"value":100}' http://127.0.0.1:8765/brightness)
  [ "$BC" = 200 ] && BR_OK=$((BR_OK+1))
done
if [ "$ADMIN_OK" -ge 5 ] && [ "$BR_OK" -ge 5 ]; then
  ok "assertable successful admin/brightness workloads admin_ok=$ADMIN_OK br_ok=$BR_OK"
else
  bad "workload counters admin_ok=$ADMIN_OK br_ok=$BR_OK"
fi

ADMIN_OK=5
GATE_FAIL=0
if [ "$ADMIN_OK" -ge 10 ]; then GATE_FAIL=0; else GATE_FAIL=1; fi
[ "$GATE_FAIL" -eq 1 ] && ok "admin success gate rejects insufficient workload" || bad "gate"

echo "RESULT pass=$PASS fail=$FAIL"
[ "$FAIL" -eq 0 ]
