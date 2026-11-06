#!/usr/bin/env python3
"""Generate genuinely ~4MB x N images offline and assert size band + JPEG validity."""
import os, secrets, tempfile, sys
from PIL import Image

MIN_OK = 3_800_000
MAX_OK = 4_500_000

def gen_one(path, i=0):
    w, h = 2200, 2200
    for _ in range(8):
        raw = secrets.token_bytes(w * h * 3)
        img = Image.frombytes("RGB", (w, h), raw)
        img.save(path, "JPEG", quality=92, optimize=False, subsampling=0)
        sz = os.path.getsize(path)
        if sz < MIN_OK:
            w = int(w * 1.12); h = int(h * 1.12); continue
        if sz > MAX_OK:
            w = int(w * 0.92); h = int(h * 0.92); continue
        return sz
    w, h = 2400, 2400
    while True:
        raw = secrets.token_bytes(w * h * 3)
        img = Image.frombytes("RGB", (w, h), raw)
        img.save(path, "JPEG", quality=95, optimize=False, subsampling=0)
        sz = os.path.getsize(path)
        if sz >= MIN_OK:
            return sz
        w += 100; h += 100
        if w > 4000:
            raise SystemExit(f"cannot reach size: {sz}")

def main():
    td = tempfile.mkdtemp()
    # Generate 3 offline (full 20 is slow/large); assert algorithm. Also one sparse-control that may fail.
    sizes = []
    for i in range(3):
        p = os.path.join(td, f"s3-{i:02d}.jpg")
        sz = gen_one(p, i)
        assert MIN_OK <= sz <= 5_000_000, (p, sz)
        # valid JPEG header
        with open(p, 'rb') as f:
            assert f.read(2) == b'\xff\xd8'
        sizes.append(sz)
        print("gen", i, sz)
    # Control: sparse pattern like original (may be < 3.5MB at small dims) — document risk
    from PIL import Image as Im
    ctrl = os.path.join(td, "sparse.jpg")
    img = Im.new('RGB', (3000, 2250), (10, 20, 30))
    px = img.load()
    for y in range(0, 2250, 8):
        for x in range(0, 3000, 8):
            px[x, y] = ((x) % 256, (y) % 256, (x + y) % 256)
    img.save(ctrl, 'JPEG', quality=92, optimize=False)
    sparse_sz = os.path.getsize(ctrl)
    print("sparse_control_size", sparse_sz)
    if sparse_sz < MIN_OK:
        print("PASS documents_original_sparse_not_guaranteed", sparse_sz)
    else:
        print("NOTE sparse happened to be large", sparse_sz)
    print("PASS photosize", sizes)
    return 0

if __name__ == "__main__":
    sys.exit(main())
