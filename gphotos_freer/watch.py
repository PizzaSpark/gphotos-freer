"""Watch a batch arrive in Google Photos (checks Google directly, not the emulator screen)."""
from __future__ import annotations

import json
import os
import sys
import time

from . import web


def arrived(page, man: list[dict], since_ms: int) -> tuple[int, int]:
    names: dict[str, int] = {}
    for m in man:
        names[m["file"].lower()] = names.get(m["file"].lower(), 0) + 1
    rec = page.evaluate("s => gpf.recentUploads(s)", since_ms)
    got = paid = 0
    for r in rec:
        n = (r.get("name") or "").lower()
        if names.get(n, 0) > 0:
            names[n] -= 1
            got += 1
            paid += 1 if r.get("takesUpSpace") else 0
    return got, paid


def watch(work: str, batch: str, since_hours: float = 12, every: int = 300, timeout_h: float = 12) -> int:
    with open(os.path.join(work, "staged", batch, "manifest.json"), encoding="utf-8") as f:
        man = json.load(f)
    since = int((time.time() - since_hours * 3600) * 1000)
    end = time.time() + timeout_h * 3600
    with web.session(work) as (ctx, page):
        last = -1
        while time.time() < end:
            page.reload(wait_until="domcontentloaded")
            page.wait_for_function("window.WIZ_global_data && window.WIZ_global_data.SNlM0e")
            with open(web.JS, encoding="utf-8") as f:
                page.evaluate(f.read())
            got, paid = arrived(page, man, since)
            if got != last:
                print(f"{time.strftime('%H:%M')}  arrived {got}/{len(man)}  (taking space: {paid})", flush=True)
                last = got
            if got >= len(man):
                return 0
            time.sleep(every)
    return 2


if __name__ == "__main__":
    sys.exit(watch("work", sys.argv[1]))
