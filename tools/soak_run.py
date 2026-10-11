#!/usr/bin/env python3
"""Memory Frame unattended soak harness (MF-06). Runs ON the Pi, as the service user.

    .venv/bin/python tools/soak_run.py --base http://127.0.0.1:5000 --minutes 60

What it does (stdlib + Pillow only):
  1. baseline: real media ids (never touched) + metrics
  2. pairs a phone session named "soak-test-<ts>" via the on-device pairing round
     (POST /pairing/start from localhost -> 6-digit code -> POST /pair with CSRF)
  3. uploads 12 generated JPEGs, 1 video, bad.jpg, bad.mp4 (POST /api/v1/media)
  4. every --interval seconds appends a row to metrics.csv
  5. at --add-remove-at minutes: upload 1 image (must appear in /media), delete one test image
  6. at the end (or SIGTERM/SIGINT): deletes ONLY ids it created (never baseline ids),
     revokes the soak session (no per-device API exists: marks its sessions revoked and
     deletes its device row directly in SQLite), verifies media == baseline
  7. writes summary.json with PASS/FAIL per criterion
"""
import argparse
import csv
import http.cookiejar
import io
import json
import os
import random
import re
import shutil
import signal
import sqlite3
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

STOP = False


def _on_signal(signum, _frame):
    global STOP
    STOP = True
    log(f"signal {signum}: stopping, cleaning up")


def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    if LOGFILE:
        with open(LOGFILE, "a") as f:
            f.write(line + "\n")


LOGFILE = None


# ---------------------------------------------------------------- HTTP client
class Client:
    def __init__(self, base, user_agent):
        self.base = base.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.ua = user_agent

    def cookie(self, name):
        for c in self.jar:
            if c.name == name:
                return c.value
        return None

    def req(self, method, path, data=None, headers=None, timeout=60):
        h = {"User-Agent": self.ua}
        h.update(headers or {})
        if method not in ("GET", "HEAD") and self.cookie("mf_csrf"):
            h.setdefault("X-CSRF-Token", self.cookie("mf_csrf"))
        r = urllib.request.Request(self.base + path, data=data, headers=h, method=method)
        try:
            with self.opener.open(r, timeout=timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()
        except (urllib.error.URLError, OSError) as e:
            return None, str(e).encode()

    def json(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        st, raw = self.req(method, path, data, {"Content-Type": "application/json"} if data else None)
        try:
            return st, json.loads(raw or b"null")
        except ValueError:
            return st, raw[:200].decode("utf-8", "replace")

    def upload(self, name, payload, ctype):
        boundary = "soak" + uuid.uuid4().hex
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{name}\"\r\n"
                f"Content-Type: {ctype}\r\n\r\n").encode() + payload + f"\r\n--{boundary}--\r\n".encode()
        st, raw = self.req("POST", "/api/v1/media", body,
                           {"Content-Type": f"multipart/form-data; boundary={boundary}"}, timeout=180)
        try:
            return st, json.loads(raw)
        except ValueError:
            return st, raw[:200].decode("utf-8", "replace")


# ---------------------------------------------------------------- test media
def make_jpeg(w, h, seed):
    from PIL import Image, ImageDraw
    rnd = random.Random(seed)
    im = Image.new("RGB", (w, h), tuple(rnd.randrange(256) for _ in range(3)))
    d = ImageDraw.Draw(im)
    for _ in range(60):
        x0, y0 = rnd.randrange(w), rnd.randrange(h)
        d.ellipse([x0, y0, x0 + rnd.randrange(w // 3 + 1), y0 + rnd.randrange(h // 3 + 1)],
                  fill=tuple(rnd.randrange(256) for _ in range(3)))
    # some texture so file sizes look like phone photos
    noise = Image.effect_noise((w, h), 25 + seed % 20).convert("RGB")
    im = Image.blend(im, noise, 0.25)
    d = ImageDraw.Draw(im)
    d.text((20, 20), f"soak {seed} {w}x{h}", fill=(255, 255, 255))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=88)
    return b.getvalue()


def make_video(workdir, use_ffmpeg=True):
    """(filename, bytes, method). ffmpeg -> MP4; else a valid MJPEG AVI written in Python."""
    if use_ffmpeg and shutil.which("ffmpeg"):
        out = os.path.join(workdir, "soak.mp4")
        r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                            "testsrc=duration=3:size=640x360:rate=15", "-pix_fmt", "yuv420p", out],
                           capture_output=True, timeout=120)
        if r.returncode == 0 and os.path.getsize(out) > 0:
            with open(out, "rb") as f:
                return "soak.mp4", f.read(), "ffmpeg mp4 (testsrc 3 s 640x360)"
    return "soak.avi", mjpeg_avi(), "python MJPEG AVI (ffmpeg not found; 30 frames 320x240)"


def mjpeg_avi(w=320, h=240, frames=30, fps=10):
    from PIL import Image, ImageDraw
    jpgs = []
    for i in range(frames):
        im = Image.new("RGB", (w, h), (i * 8 % 256, 40, 120))
        ImageDraw.Draw(im).rectangle([i * 8, 80, i * 8 + 60, 160], fill=(255, 255, 0))
        b = io.BytesIO()
        im.save(b, "JPEG", quality=80)
        jpgs.append(b.getvalue())

    def chunk(fourcc, data):
        pad = b"\0" if len(data) % 2 else b""
        return fourcc + struct.pack("<I", len(data)) + data + pad

    def lst(kind, data):
        return b"LIST" + struct.pack("<I", len(data) + 4) + kind + data

    maxsz = max(len(j) for j in jpgs)
    avih = struct.pack("<IIIIIIIIII4I", 1000000 // fps, maxsz * fps, 0, 0x10, frames, 0, 1, maxsz, w, h, 0, 0, 0, 0)
    strh = struct.pack("<4s4sIHHIIIIIIIIhhhh", b"vids", b"MJPG", 0, 0, 0, 0, 1, fps, 0, frames, maxsz,
                       0xFFFFFFFF, 0, 0, 0, w, h)
    strf = struct.pack("<IiiHH4sIiiII", 40, w, h, 1, 24, b"MJPG", w * h * 3, 0, 0, 0, 0)
    hdrl = lst(b"hdrl", chunk(b"avih", avih) + lst(b"strl", chunk(b"strh", strh) + chunk(b"strf", strf)))
    movi_items, idx, off = b"", b"", 4
    for j in jpgs:
        c = chunk(b"00dc", j)
        idx += struct.pack("<4sIII", b"00dc", 0x10, off, len(j))
        off += len(c)
        movi_items += c
    body = b"AVI " + hdrl + lst(b"movi", movi_items) + chunk(b"idx1", idx)
    return b"RIFF" + struct.pack("<I", len(body)) + body


# ---------------------------------------------------------------- metrics
def sh(cmd, timeout=10):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout
    except (OSError, subprocess.SubprocessError):
        return None, ""


def meminfo_available_kb():
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1])
    except OSError:
        pass
    return None


def rss_kb(pid):
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except (OSError, ValueError):
        pass
    return None


def service_info(unit):
    rc, out = sh(["systemctl", "show", unit, "-p", "MainPID", "-p", "NRestarts"])
    info = dict(l.split("=", 1) for l in out.splitlines() if "=" in l)
    pid = int(info["MainPID"]) if info.get("MainPID", "").isdigit() else None
    nr = int(info["NRestarts"]) if info.get("NRestarts", "").isdigit() else None
    return pid if pid else None, nr


def chromium_stats():
    total, count = 0, 0
    for p in os.listdir("/proc"):
        if not p.isdigit():
            continue
        try:
            with open(f"/proc/{p}/comm") as f:
                comm = f.read().strip()
        except OSError:
            continue
        if comm.startswith("chromium") or comm in ("chrome", "chromium-browse"):
            count += 1
            total += rss_kb(p) or 0
    return total, count


def vcgencmd():
    temp = throttled = None
    rc, out = sh(["vcgencmd", "measure_temp"])
    m = re.search(r"temp=([\d.]+)", out or "")
    if m:
        temp = float(m.group(1))
    rc, out = sh(["vcgencmd", "get_throttled"])
    m = re.search(r"throttled=(0x[0-9a-fA-F]+)", out or "")
    if m:
        throttled = m.group(1)
    return temp, throttled


def journal_warnings(unit, seconds=60):
    rc, out = sh(["journalctl", "-u", unit, "--since", f"-{seconds}s", "-p", "warning", "-q", "--no-pager"])
    if rc is None:
        return None
    lines = [l for l in out.splitlines() if l.strip() and l.strip() != "-- No entries --"]
    return len(lines)


def load1():
    try:
        return float(open("/proc/loadavg").read().split()[0])
    except (OSError, ValueError, IndexError):
        return None


FIELDS = ["time", "elapsed_s", "mem_available_kb", "main_pid", "app_rss_kb", "n_restarts", "chromium_rss_kb",
          "chromium_procs", "temp_c", "throttled", "healthz", "media_count", "load1", "journal_warnings"]


def sample(cli, unit, t0):
    pid, nr = service_info(unit)
    crss, cn = chromium_stats()
    temp, thr = vcgencmd()
    hz, _ = cli.req("GET", "/healthz", timeout=10)
    st, media = cli.json("GET", "/media")
    return {"time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "elapsed_s": round(time.time() - t0),
            "mem_available_kb": meminfo_available_kb(), "main_pid": pid, "app_rss_kb": rss_kb(pid) if pid else None,
            "n_restarts": nr, "chromium_rss_kb": crss, "chromium_procs": cn, "temp_c": temp, "throttled": thr,
            "healthz": hz, "media_count": len(media) if st == 200 and isinstance(media, list) else None,
            "load1": load1(), "journal_warnings": journal_warnings(unit)}


# ---------------------------------------------------------------- main flow
class Soak:
    def __init__(self, a):
        self.a = a
        self.ts = time.strftime("%Y%m%d-%H%M%S")
        self.name = f"soak-test-{self.ts}"
        self.cli = Client(a.base, self.name)
        self.local = Client(a.base, "soak-frame")      # plays the on-device kiosk (localhost)
        self.baseline_ids = None
        self.created = []
        self.uploads = []
        self.checks = {}
        self.cleaned = False
        self.paired_at = None

    def api_ids(self):
        st, items = self.cli.json("GET", "/api/v1/media")
        if st != 200 or not isinstance(items, list):
            raise RuntimeError(f"GET /api/v1/media -> {st} {items}")
        return {int(i["id"]) for i in items}

    def pair(self):
        self.local.req("GET", "/healthz")
        self.cli.req("GET", "/healthz")                     # sets mf_csrf
        st, state = self.local.json("POST", "/pairing/start")
        if st != 200 or not (state or {}).get("round"):
            raise RuntimeError(f"POST /pairing/start -> {st} {state}")
        code = state["round"]["code"]
        self.paired_at = time.time() - 1
        st, body = self.cli.json("POST", "/pair", {"code": code})
        if st != 200 or not self.cli.cookie("mf_session"):
            raise RuntimeError(f"POST /pair -> {st} {body}")
        log(f"paired as '{self.name}'")

    def up(self, name, data, ctype, expect_ok=True):
        t = time.time()
        st, body = self.cli.upload(name, data, ctype)
        rec = {"name": name, "bytes": len(data), "status": st, "seconds": round(time.time() - t, 2),
               "response": body}
        self.uploads.append(rec)
        if st == 201 and isinstance(body, dict):
            new = self.api_ids() - self.baseline_ids - set(self.created)
            self.created.extend(sorted(new))
            rec["ids"] = sorted(new)
        log(f"upload {name} ({len(data)//1024} KB) -> {st} {body if st != 201 else rec.get('ids')}")
        return st, rec

    def upload_all(self, workdir):
        sizes = [(4000, 3000), (4000, 3000), (4000, 3000), (3000, 4000), (1080, 1920), (2560, 1440),
                 (1920, 1080), (1600, 1200), (1280, 960), (1000, 750), (800, 600), (3264, 2448)]
        for i, (w, h) in enumerate(sizes):
            self.up(f"soak_{i:02d}_{w}x{h}.jpg", make_jpeg(w, h, 1000 + i + int(self.ts[-6:])), "image/jpeg")
        vname, vdata, how = make_video(workdir, not self.a.no_ffmpeg)
        self.video_method = how
        self.up(vname, vdata, "video/mp4" if vname.endswith(".mp4") else "video/x-msvideo")
        self.up("bad.jpg", os.urandom(20000), "image/jpeg")
        self.up("bad.mp4", os.urandom(20000), "video/mp4")

    def add_remove(self):
        st, rec = self.up("soak_add_20min.jpg", make_jpeg(2000, 1500, 777 + int(self.ts[-6:])), "image/jpeg")
        added = rec.get("ids") or []
        visible = bool(added) and added[0] in self.api_ids()
        stored = (rec.get("response") or {}).get("id") if isinstance(rec.get("response"), dict) else None
        sd, media = self.cli.json("GET", "/media")      # what the slideshow sees
        display_ok = sd == 200 and isinstance(media, list) and any(m.get("filename") == stored for m in media)
        victim = next((i for i in self.created if i not in self.baseline_ids and i not in added), None)
        removed = False
        if victim is not None:
            sdel, _ = self.cli.req("DELETE", f"/api/v1/media/{victim}")
            removed = sdel == 200 and victim not in self.api_ids()
            if removed:
                self.created.remove(victim)
            log(f"delete test id {victim} -> {sdel}; gone={removed}")
        self.checks["add_remove"] = {"added_ids": added, "added_visible": visible, "display_media_ok": display_ok,
                                     "deleted_id": victim, "deleted_gone": removed,
                                     "ok": bool(st == 201 and visible and display_ok and removed)}

    def revoke(self):
        """No per-device revoke API exists (only sign-out-all, which would sign out real phones).
        Revoke just our device directly in SQLite: sessions revoked, then the device row deleted."""
        if not self.paired_at:
            return {"ok": True, "note": "never paired"}
        try:
            conn = sqlite3.connect(self.a.db, timeout=15)
            rows = conn.execute("SELECT id FROM devices WHERE name = ? AND created_at >= ?",
                                (self.name, self.paired_at - 60)).fetchall()
            if len(rows) != 1:
                conn.close()
                return {"ok": False, "note": f"expected exactly 1 device named {self.name}, found {len(rows)}"}
            dev = rows[0][0]
            n = conn.execute("UPDATE sessions SET revoked = 1 WHERE device_id = ?", (dev,)).rowcount
            conn.execute("DELETE FROM sessions WHERE device_id = ?", (dev,))
            conn.execute("DELETE FROM devices WHERE id = ?", (dev,))
            conn.commit()
            conn.close()
        except sqlite3.Error as e:
            return {"ok": False, "note": f"sqlite: {e}"}
        st, _ = self.cli.json("GET", "/api/v1/status")
        return {"ok": st == 401, "device_id": dev, "sessions_revoked": n, "api_after_revoke": st}

    def cleanup(self):
        if self.cleaned:
            return self.checks.get("cleanup")
        self.cleaned = True
        res = {"deleted": [], "failed": [], "refused_baseline": []}
        if self.baseline_ids is not None:
            try:
                current = self.api_ids()
            except RuntimeError as e:
                current = set(self.created)
                res["note"] = str(e)
            for mid in sorted(set(self.created) | (current - self.baseline_ids)):
                if mid in self.baseline_ids:          # double check: never touch real photos
                    res["refused_baseline"].append(mid)
                    continue
                if mid not in self.created:
                    # appeared during the run but not uploaded by us (e.g. the user uploaded): keep
                    res.setdefault("kept_foreign", []).append(mid)
                    continue
                st, _ = self.cli.req("DELETE", f"/api/v1/media/{mid}")
                (res["deleted"] if st in (200, 404) else res["failed"]).append([mid, st])
            try:
                final = self.api_ids()
            except RuntimeError:
                final = None
            res["final_equals_baseline"] = final == self.baseline_ids or (
                final is not None and final - set(res.get("kept_foreign", [])) == self.baseline_ids)
        res["revoke"] = self.revoke()
        res["ok"] = bool(res.get("final_equals_baseline")) and not res["failed"] and res["revoke"].get("ok")
        self.checks["cleanup"] = res
        log(f"cleanup: {json.dumps(res)}")
        return res


def avg(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return sum(xs) / len(xs) if xs else None


def evaluate(rows, soak, window):
    k = max(1, min(window, len(rows) // 2 or 1))
    def growth(field):
        a, b = avg(r[field] for r in rows[:k]), avg(r[field] for r in rows[-k:])
        if not a or b is None:
            return None, a, b
        return (b - a) / a * 100, a, b
    res = {}
    res["healthz_always_200"] = all(r["healthz"] == 200 for r in rows)
    nrs = {r["n_restarts"] for r in rows}
    pids = {r["main_pid"] for r in rows}
    res["nrestarts_unchanged"] = len(nrs) == 1 and None not in nrs
    res["mainpid_unchanged"] = len(pids) == 1 and None not in pids
    for f in ("app_rss_kb", "chromium_rss_kb"):
        g, a, b = growth(f)
        res[f"{f}_growth_pct"] = None if g is None else round(g, 1)
        res[f"{f}_growth_ok"] = None if g is None else g < 20
    temps = [r["temp_c"] for r in rows if r["temp_c"] is not None]
    res["max_temp_c"] = max(temps) if temps else None
    res["temp_ok"] = None if not temps else max(temps) < 80
    thr = {r["throttled"] for r in rows if r["throttled"]}
    res["throttled_values"] = sorted(thr)
    res["throttled_ok"] = None if not thr else thr == {"0x0"}
    res["add_remove_ok"] = (None if STOP and "add_remove" not in soak.checks   # stopped before it ran
                            else soak.checks.get("add_remove", {}).get("ok", False))
    res["cleanup_ok"] = bool(soak.checks.get("cleanup", {}).get("ok"))
    crit = ["healthz_always_200", "nrestarts_unchanged", "mainpid_unchanged", "app_rss_kb_growth_ok",
            "chromium_rss_kb_growth_ok", "temp_ok", "throttled_ok", "add_remove_ok", "cleanup_ok"]
    res["criteria"] = {c: ("PASS" if res[c] is True else "FAIL" if res[c] is False else "N/A") for c in crit}
    res["overall"] = "FAIL" if "FAIL" in res["criteria"].values() else "PASS"
    return res


def main():
    global LOGFILE
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="http://127.0.0.1:5000")
    ap.add_argument("--minutes", type=float, default=60)
    ap.add_argument("--interval", type=float, default=60, help="seconds between metric rows")
    ap.add_argument("--add-remove-at", type=float, default=20, help="minutes")
    ap.add_argument("--growth-window", type=int, default=5, help="rows averaged at start/end")
    ap.add_argument("--unit", default="memoryframe")
    ap.add_argument("--db", default=os.environ.get("MEMORYFRAME_DB",
                    os.path.join(os.environ.get("MEMORYFRAME_DATA_DIR", "/var/lib/memoryframe"), "memoryframe.db")))
    ap.add_argument("--out", default=None)
    ap.add_argument("--no-ffmpeg", action="store_true", help="force the pure-Python MJPEG AVI video")
    a = ap.parse_args()
    out = os.path.expanduser(a.out or f"~/soak-{time.strftime('%Y%m%d-%H%M%S')}")
    os.makedirs(out, exist_ok=True)
    LOGFILE = os.path.join(out, "soak.log")
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    soak = Soak(a)
    rows, summary = [], {"started": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "args": vars(a), "out": out}
    t0 = time.time()
    csv_path = os.path.join(out, "metrics.csv")
    try:
        soak.pair()
        soak.baseline_ids = soak.api_ids()
        summary["baseline_ids"] = sorted(soak.baseline_ids)
        rows.append(sample(soak.cli, a.unit, t0))
        summary["baseline_metrics"] = rows[0]
        log(f"baseline: {len(soak.baseline_ids)} media ids {sorted(soak.baseline_ids)}; {rows[0]}")
        with open(csv_path, "w", newline="") as f:
            w = csv.DictWriter(f, FIELDS)
            w.writeheader()
            w.writerow(rows[0])
        soak.upload_all(out)
        summary["video_method"] = soak.video_method
        did_add = False
        next_t = time.time() + a.interval
        end = t0 + a.minutes * 60
        while not STOP and time.time() < end:
            time.sleep(min(1.0, max(0.0, next_t - time.time())))
            if not did_add and time.time() - t0 >= a.add_remove_at * 60:
                soak.add_remove()
                did_add = True
            if time.time() >= next_t:
                r = sample(soak.cli, a.unit, t0)
                rows.append(r)
                with open(csv_path, "a", newline="") as f:
                    csv.DictWriter(f, FIELDS).writerow(r)
                log("sample " + " ".join(f"{k}={r[k]}" for k in FIELDS[1:]))
                next_t += a.interval
        if not did_add and not STOP:
            soak.add_remove()
    except Exception as e:  # noqa: BLE001 - always clean up
        log(f"ERROR: {type(e).__name__}: {e}")
        summary["error"] = f"{type(e).__name__}: {e}"
    finally:
        soak.cleanup()
        if soak.baseline_ids is not None:
            rows.append(sample(soak.cli, a.unit, t0))
            with open(csv_path, "a", newline="") as f:
                csv.DictWriter(f, FIELDS).writerow(rows[-1])
        summary.update({"finished": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "interrupted": STOP,
                        "uploads": soak.uploads, "checks": soak.checks, "samples": len(rows)})
        summary["result"] = evaluate(rows, soak, a.growth_window) if rows else {"overall": "FAIL"}
        if "error" in summary:
            summary["result"]["overall"] = "FAIL"
        elif STOP and summary["result"].get("overall") == "PASS":
            summary["result"]["overall"] = "INCOMPLETE"      # stopped early: cleanup done, not a full soak
        with open(os.path.join(out, "summary.json"), "w") as f:
            json.dump(summary, f, indent=2, default=str)
        log(f"RESULT {summary['result'].get('overall')}: {json.dumps(summary['result'].get('criteria'))}")
        log(f"output in {out}")
    return 0 if summary["result"].get("overall") == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
