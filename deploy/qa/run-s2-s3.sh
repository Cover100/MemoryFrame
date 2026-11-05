#!/bin/bash
set -eu
REPO=$HOME/Documents/GitHub/MemoryFrame
OUT=/root/sd-wear
BASE=$HOME/sdwear-s2s3
JAR=$BASE/cookies.txt
LOG=$BASE/run.log
PHOTOS=$BASE/photos
CAP=$REPO/deploy/sd-wear-capture.sh
HELPER=$BASE/csrf.py
mkdir -p "$BASE" "$PHOTOS"
cd "$REPO"
: > "$LOG"
exec > >(tee -a "$LOG") 2>&1
echo "=== start $(date -Is) HEAD=$(git rev-parse --short HEAD) mode=$(cat /boot/firmware/memoryframe-mode.txt) ==="
test "$(cat /boot/firmware/memoryframe-mode.txt)" = dev
test -f "$CAP"

cat > "$HELPER" << 'PY'
import http.cookiejar, sys
path = sys.argv[1]
name = sys.argv[2] if len(sys.argv) > 2 else "mf_csrf"
cj = http.cookiejar.MozillaCookieJar(path)
cj.load(ignore_discard=True, ignore_expires=True)
for c in cj:
    if c.name == name:
        print(c.value)
        raise SystemExit(0)
raise SystemExit("cookie %s not found in %s" % (name, path))
PY

rm -f "$JAR"
# seed cookies (csrf) via GET /pair
curl -s -c "$JAR" -b "$JAR" -o /dev/null http://127.0.0.1/pair || true
CSRF=$(python3 "$HELPER" "$JAR")
echo "csrf_len=${#CSRF}"
# start pairing round (localhost)
CODE=$(curl -s -X POST http://127.0.0.1/pairing/start | python3 -c 'import sys,json; print(json.load(sys.stdin)["round"]["code"])')
echo "pair_code=$CODE"
# redeem with CSRF
PAIR_RESP=$(curl -s -c "$JAR" -b "$JAR" -w "\nHTTP:%{http_code}" -X POST \
  -H 'Content-Type: application/json' -H 'Origin: http://127.0.0.1' -H "X-CSRF-Token: $CSRF" \
  -d "{\"code\":\"$CODE\"}" http://127.0.0.1/pair || true)
echo "$PAIR_RESP" | tail -3
CSRF=$(python3 "$HELPER" "$JAR")
ADMIN=$(curl -s -c "$JAR" -b "$JAR" -o /dev/null -w "%{http_code}" http://127.0.0.1/admin/)
echo "admin=$ADMIN"
test "$ADMIN" = "200"

echo "=== warmup 10m $(date -Is) ==="
sleep 600

echo "=== S2-t0 $(date -Is) ==="
sudo "$CAP" S2-t0
S2_START=$(date +%s)
MAX=$(curl -s http://127.0.0.1/brightness | python3 -c 'import sys,json; print(json.load(sys.stdin).get("maximum",255))' || echo 255)
MIN=$(curl -s http://127.0.0.1/brightness | python3 -c 'import sys,json; print(json.load(sys.stdin).get("minimum",40))' || echo 40)
echo "brightness min=$MIN max=$MAX"
sudo bash -c "pidstat -d 60 30 > $OUT/S2-pidstat.txt 2>&1" &
sudo bash -c "iotop -obPa -d 60 -n 30 -qqq > $OUT/S2-iotop.txt 2>&1" &
sudo bash -c "iostat -dkx mmcblk0 60 30 > $OUT/S2-iostat.txt 2>&1" &
sudo bash -c "inotifywait -m -r -e close_write,create,delete,moved_to --timefmt %T --format '%T %e %w%f' /var/lib/memoryframe /home/memoryframe/.cache /var/log /etc /var/lib/systemd -o $OUT/S2-iw.log" &
IW=$!
i=0
while [ $(($(date +%s)-S2_START)) -lt 1800 ]; do
  curl -s -c "$JAR" -b "$JAR" -o /dev/null http://127.0.0.1/admin/ || true
  curl -s -c "$JAR" -b "$JAR" -o /dev/null http://127.0.0.1/api/v1/status || true
  VAL=$(( MIN + (i % 20) * (MAX - MIN) / 20 ))
  curl -s -X POST -H 'Content-Type: application/json' -d "{\"value\":$VAL}" http://127.0.0.1/brightness >/dev/null || true
  VAL2=$(( MAX - (i % 20) * (MAX - MIN) / 20 ))
  curl -s -X POST -H 'Content-Type: application/json' -d "{\"value\":$VAL2}" http://127.0.0.1/brightness >/dev/null || true
  i=$((i+1)); sleep 2
done
sudo kill $IW 2>/dev/null || true
wait || true
echo "=== S2-t1 $(date -Is) loops=$i ==="
sudo "$CAP" S2-t1
python3 - <<'PY'
import re, pathlib
out=pathlib.Path('/root/sd-wear')
def card(label):
    t=(out/f'{label}.txt').read_text()
    m=re.search(r'card_bytes_written=(\d+)', t)
    d=re.search(r'date=(.+)', t)
    u=re.search(r'uptime_s=([0-9.]+)', t)
    return int(m.group(1)), (d.group(1) if d else ''), float(u.group(1) if u else 0)
t0,d0,u0=card('S2-t0'); t1,d1,u1=card('S2-t1')
hours=max((u1-u0)/3600.0, 1e-9)
mb_h=(t1-t0)/1048576.0/hours
print(f'S2 card_bytes delta={t1-t0} hours={hours:.4f} MB_h={mb_h:.2f} limit=40 pass={mb_h<=40}')
print(f'S2 dates {d0} -> {d1}')
print((out/'S2-t0.txt').read_text()); print('---'); print((out/'S2-t1.txt').read_text())
PY

echo "=== gen S3 photos $(date -Is) ==="
python3 - <<'PY'
from PIL import Image
import os
out=os.path.expanduser('~/sdwear-s2s3/photos')
os.makedirs(out, exist_ok=True)
for i in range(20):
    img=Image.new('RGB',(3000,2250),((i*37)%256,(i*73)%256,(i*19)%256))
    px=img.load()
    for y in range(0,2250,8):
        for x in range(0,3000,8):
            px[x,y]=((x+i)%256,(y+i*3)%256,(x+y+i)%256)
    path=f'{out}/s3-{i:02d}.jpg'
    q=92
    img.save(path,'JPEG',quality=q,optimize=False)
    while os.path.getsize(path)<3800000 and q<98:
        q+=1; img.save(path,'JPEG',quality=q,optimize=False)
    if os.path.getsize(path)<3500000:
        img=img.resize((4000,3000)); img.save(path,'JPEG',quality=95,optimize=False)
    print(i, os.path.getsize(path))
PY

curl -s -c "$JAR" -b "$JAR" -o /dev/null http://127.0.0.1/admin/
CSRF=$(python3 "$HELPER" "$JAR")
echo "=== S3-t0 $(date -Is) ==="
sudo "$CAP" S3-t0
UP=0; OK=0; FAIL=0
for f in "$PHOTOS"/s3-*.jpg; do
  SZ=$(stat -c%s "$f")
  RESP=$(curl -s -c "$JAR" -b "$JAR" -w "\nHTTP:%{http_code}" -X POST \
    -H 'Origin: http://127.0.0.1' -H "X-CSRF-Token: $CSRF" \
    -F "file=@${f};type=image/jpeg" http://127.0.0.1/api/v1/media || true)
  echo "$RESP" | tail -2
  CODE=$(echo "$RESP" | sed -n 's/.*HTTP:\([0-9]*\).*/\1/p' | tail -1)
  if [ "$CODE" = 201 ] || [ "$CODE" = 200 ]; then OK=$((OK+1)); UP=$((UP+SZ)); else FAIL=$((FAIL+1)); fi
  if [ $((OK % 5)) -eq 0 ]; then
    curl -s -c "$JAR" -b "$JAR" -o /dev/null http://127.0.0.1/admin/
    CSRF=$(python3 "$HELPER" "$JAR")
  fi
done
echo "=== S3-t1 $(date -Is) uploaded=$UP ok=$OK fail=$FAIL ==="
sudo "$CAP" S3-t1
python3 - <<PY
import re, pathlib
out=pathlib.Path('/root/sd-wear')
def card(label):
    t=(out/f'{label}.txt').read_text()
    m=re.search(r'card_bytes_written=(\d+)', t)
    u=re.search(r'uptime_s=([0-9.]+)', t)
    return int(m.group(1)), float(u.group(1) if u else 0)
t0,u0=card('S3-t0'); t1,u1=card('S3-t1')
up=int('$UP')
amp=(t1-t0)/up if up else float('inf')
print(f'S3 card_delta={t1-t0} uploaded={up} amplification={amp:.3f} limit=4 pass={amp<=4}')
print((out/'S3-t0.txt').read_text()); print('---'); print((out/'S3-t1.txt').read_text())
PY
journalctl --disk-usage || true
findmnt /run/mfkiosk || true
echo "MemAvailable=$(awk '/MemAvailable/{print \$2}' /proc/meminfo)"
findmnt -no OPTIONS / || true
echo DONE > "$BASE/DONE"
echo "=== finished $(date -Is) ==="
