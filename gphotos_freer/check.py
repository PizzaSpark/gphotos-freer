"""`check`: is uploading still free?

Uploads one tiny, unique test image the same way the tool uploads everything, asks Google
whether it takes up storage, then moves the test image to Trash. Exit code 0 = still free,
1 = it counts (stop using the tool), 2 = couldn't tell.
"""
from __future__ import annotations

import json
import os
import random
import struct
import tempfile
import time
import zlib

from . import auth, web


def _random_png(path: str, size: int = 96):
    rows = b"".join(b"\x00" + os.urandom(size * 3) for _ in range(size))
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(png)


def check(work: str = "work", keep: bool = False) -> int:
    token = auth.load(work)
    if not token:
        print("No sign-in token yet. Run: python -m gphotos_freer auth")
        return 2
    from gpmc import Client
    tmp = tempfile.mkdtemp()
    name = f"gphotos-freer-check-{time.strftime('%Y%m%d-%H%M%S')}-{random.randint(1000, 9999)}.png"
    path = os.path.join(tmp, name)
    _random_png(path)
    print(f"Uploading a tiny test image ({name})...")
    out = Client(auth_data=token).upload(target=path, show_progress=False)
    mk = next(iter(out.values()), None)
    os.remove(path)
    os.rmdir(tmp)
    if not mk:
        print("Upload failed; can't tell.")
        return 2

    with web.session(work) as (ctx, page):
        info = {}
        for _ in range(10):  # the flag can take a few seconds to appear
            info = page.evaluate("ks => gpf.spaceInfo(ks)", [mk]).get(mk) or {}
            if info.get("takesUpSpace") is not None:
                break
            time.sleep(3)
        if not keep:
            dk = page.evaluate("mk => gpf.rpc('VrseUb',[mk,null,null,null,null]).then(i => i?.[0]?.[3])", mk)
            if dk:
                page.evaluate("dks => gpf.trash(dks)", [dk])

    result = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "free": info.get("takesUpSpace") is False,
              "original": info.get("original"), "raw": info}
    with open(os.path.join(work, "check-history.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(result) + "\n")

    if info.get("takesUpSpace") is False:
        print("STILL FREE: the test upload doesn't take up storage" + (" (original quality)." if info.get("original") else "."))
        return 0
    if info.get("takesUpSpace") is True:
        print("NOT FREE ANYMORE: the test upload counts toward storage. Google may have closed the trick.")
        print("Don't upload more until this is sorted out. (The test image was moved to Trash.)")
        return 1
    print("Couldn't read the storage flag from Google; try again later.")
    return 2
