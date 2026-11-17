"""Find, download and trash Google Photos items through the photos.google.com web API.

Runs in the tool's own Chrome window (work/browser-profile), which the user signs in to once.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import html
import json
import os
import re
import time

from . import deleter
from .prepare import ExifTool, MARKER, _unique_path

JS = os.path.join(os.path.dirname(__file__), "webapi.js")
VIDEO_EXTS = (".mp4", ".mov", ".m4v", ".3gp", ".mkv", ".webm", ".avi")


@contextlib.contextmanager
def session(work: str):
    """Yield (context, page) with the gpf helpers loaded in a signed-in photos.google.com page."""
    from playwright.sync_api import sync_playwright
    print("Using the tool's own Chrome window (separate profile, not your normal Chrome).")
    deleter.start_chrome(os.path.join(work, "browser-profile"))
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{deleter.PORT}")
        ctx = browser.contexts[0]
        page = ctx.new_page()
        page.set_default_timeout(60000)
        try:
            deleter.wait_for_sign_in(ctx, page)
            page.wait_for_function("window.WIZ_global_data && window.WIZ_global_data.SNlM0e")
            with open(JS, encoding="utf-8") as f:
                page.evaluate(f.read())
            yield ctx, page
        finally:
            page.close()


def _kind(x: dict) -> str:
    if (x.get("name") or "").lower().endswith(VIDEO_EXTS):
        return "video"
    return "shared" if x.get("owned") is False else "own"


# ---------------------------------------------------------------- scan

def scan(work: str) -> list[dict]:
    with session(work) as (ctx, page):
        print("Scanning your library (read-only)...")
        page.evaluate("window.__gpfProgress = null")
        res = page.evaluate("async () => await gpf.scan()")
    found = res["found"]
    path = os.path.join(work, "space-scan.json")
    os.makedirs(work, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"scanned": time.strftime("%Y-%m-%d %H:%M:%S"), "items": res["items"], "found": found}, f)
    total = sum(int(x.get("spaceTaken") or 0) for x in found)
    print(f"Checked {res['items']} items. {len(found)} take up space: {total/1e9:.2f} GB.")
    groups: dict[str, list] = {}
    for x in found:
        groups.setdefault(_kind(x), []).append(x)
    names = {"shared": "saved from someone's shared album", "own": "your own uploads", "video": "videos"}
    for k, xs in groups.items():
        print(f"  {len(xs):6} {names[k]:36} {sum(int(x.get('spaceTaken') or 0) for x in xs)/1e6:8.0f} MB")
    print(f"Saved: {path}")
    return found


# ---------------------------------------------------------------- download

def _taken_dt(x: dict) -> dt.datetime | None:
    if not x.get("taken"):
        return None
    tz = dt.timezone(dt.timedelta(milliseconds=int(x.get("tzOffset") or 0)))
    return dt.datetime.fromtimestamp(int(x["taken"]) / 1000, tz)


async def _download_async(work: str, todo: list[dict], workers: int, on_file):
    """Fetch originals concurrently with the signed-in Chrome context (keeps Google's cookies)."""
    import asyncio
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{deleter.PORT}")
        ctx = browser.contexts[0]
        page = await ctx.new_page()
        try:
            await page.goto("https://photos.google.com/", wait_until="domcontentloaded")
            await page.wait_for_function("window.WIZ_global_data && window.WIZ_global_data.SNlM0e")
            with open(JS, encoding="utf-8") as f:
                await page.evaluate(f.read())
            sem = asyncio.Semaphore(workers)

            async def one(x, url):
                async with sem:
                    for attempt in range(3):
                        try:
                            r = await ctx.request.get(url, timeout=600000)
                            ctype = r.headers.get("content-type", "")
                            if r.status == 200 and not ctype.startswith("text/"):
                                on_file(x, await r.body(), None)
                                return
                            err = f"HTTP {r.status} {ctype}"
                        except Exception as e:
                            err = str(e)[:100]
                        await asyncio.sleep(2 * (attempt + 1))
                    on_file(x, None, err)

            no_link = []
            for s in range(0, len(todo), 100):
                chunk = todo[s:s + 100]
                urls = await page.evaluate("mks => gpf.originalUrls(mks)", [x["mediaKey"] for x in chunk])
                no_link += [x for x in chunk if not urls.get(x["mediaKey"])]
                await asyncio.gather(*[one(x, urls[x["mediaKey"]]) for x in chunk if urls.get(x["mediaKey"])])
            # Videos etc.: use the website's zip download, 20 items per zip.
            import io, zipfile
            for s in range(0, len(no_link), 20):
                group = no_link[s:s + 20]
                z = await page.evaluate("ks => gpf.zipUrl(ks)", [x["mediaKey"] for x in group])
                body = None
                if z:
                    r = await ctx.request.get(z["url"], timeout=1800000)
                    body = await r.body() if r.status == 200 else None
                if not body:
                    for x in group:
                        on_file(x, None, "zip download failed")
                    continue
                if body[:2] != b"PK":  # a single item comes back as the plain file
                    on_file(group[0], body, None)
                    continue
                zf = zipfile.ZipFile(io.BytesIO(body))
                by_name = {os.path.basename(n).lower(): n for n in zf.namelist()}
                for x in group:
                    n = by_name.get((x.get("name") or "").lower())
                    on_file(x, zf.read(n) if n else None, None if n else "not in zip")
        finally:
            await page.close()


def download(work: str, batch: str, which: str = "all", limit: int | None = None, workers: int = 8,
             max_bytes: int | None = None, exclude: set | None = None) -> int:
    import asyncio
    with open(os.path.join(work, "space-scan.json"), encoding="utf-8") as f:
        found = json.load(f)["found"]
    sel = [x for x in found if (which == "all" or _kind(x) == which) and x["mediaKey"] not in (exclude or set())]
    if limit:
        sel = sel[:limit]
    if max_bytes:  # take items (oldest first in scan order) until the chunk is full
        picked, total = [], 0
        for x in sel:
            sz = int(x.get("size") or 0)
            if picked and total + sz > max_bytes:
                break
            picked.append(x)
            total += sz
        sel = picked
    out = os.path.join(work, "staged", batch)
    os.makedirs(out, exist_ok=True)
    man_path = os.path.join(out, "manifest.json")
    manifest = json.load(open(man_path, encoding="utf-8")) if os.path.exists(man_path) else []
    have = {m["mediaKey"] for m in manifest}
    in_manifest = {m["file"] for m in manifest}
    for f in os.listdir(out):  # leftovers from an interrupted run
        if f != "manifest.json" and f not in in_manifest:
            os.remove(os.path.join(out, f))
    todo = [x for x in sel if x["mediaKey"] not in have]
    print(f"Downloading {len(todo)} originals into {out} ({len(have)} already there), {workers} at a time...")

    def save():
        with open(man_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=1)

    et = ExifTool()
    stats = {"ok": 0, "failed": 0}

    def on_file(x, body, err):
        if body is None:
            stats["failed"] += 1
            print(f"  failed: {x.get('name')} ({err})")
            return
        name = x.get("name") or x["mediaKey"]
        path = _unique_path(out, name)
        with open(path, "wb") as f:
            f.write(body)
        _mark(et, path, _taken_dt(x))
        manifest.append({"file": os.path.basename(path), "name": name, "mediaKey": x["mediaKey"],
                         "dedupKey": x["dedupKey"], "url": f"https://photos.google.com/photo/{x['mediaKey']}",
                         "taken": _taken_dt(x).isoformat() if x.get("taken") else "",
                         "size": os.path.getsize(path), "spaceTaken": x.get("spaceTaken"), "kind": _kind(x)})
        stats["ok"] += 1
        if stats["ok"] % 50 == 0:
            save()
            print(f"  {stats['ok']}/{len(todo)}")

    print("Using the tool's own Chrome window (separate profile, not your normal Chrome).")
    deleter.start_chrome(os.path.join(work, "browser-profile"))
    try:
        asyncio.run(_download_async(work, todo, workers, on_file))
    finally:
        et.close()
        save()
    print(f"Done: {len(manifest)} files, {sum(m['size'] for m in manifest)/1e6:.0f} MB in {out}. "
          f"Failed: {stats['failed']} (re-run to retry).")
    return 0


def _mark(et: ExifTool, path: str, taken: dt.datetime | None):
    """Add the marker tag (so Google doesn't skip it as a duplicate), fill in a missing date,
    and set the file time to the date taken."""
    is_video = path.lower().endswith(VIDEO_EXTS)
    args = ["-overwrite_original", "-m", f"-XMP-xmp:CreatorTool={MARKER}"]
    if taken:
        has = et.run("-s3", "-DateTimeOriginal" if not is_video else "-QuickTime:CreateDate", path).strip()
        if not has or has.startswith("0000"):
            if is_video:
                u = taken.astimezone(dt.timezone.utc).strftime("%Y:%m:%d %H:%M:%S")
                args += [f"-QuickTime:CreateDate={u}", f"-QuickTime:ModifyDate={u}"]
            else:
                off = taken.strftime("%z")
                args += [f"-DateTimeOriginal={taken.strftime('%Y:%m:%d %H:%M:%S')}",
                         f"-OffsetTimeOriginal={off[:3]}:{off[3:]}"]
    et.run(*args, path)
    if taken:
        os.utime(path, (taken.timestamp(), taken.timestamp()))


# ---------------------------------------------------------------- verify + trash

def _load_manifest(work: str, batch: str) -> list[dict]:
    with open(os.path.join(work, "staged", batch, "manifest.json"), encoding="utf-8") as f:
        return json.load(f)


def trash(work: str, batch: str, since_hours: float = 48, yes: bool = False) -> int:
    """Check every new copy arrived (free), then move the old copies that still take space to Trash."""
    man = [m for m in _load_manifest(work, batch) if m.get("mediaKey") and m.get("dedupKey")]
    if not man:
        print("This batch has no items with Google IDs (was it made by `download`?).")
        return 1
    with session(work) as (ctx, page):
        since = int((time.time() - since_hours * 3600) * 1000)
        print("Checking the new copies arrived...")
        recent = page.evaluate("s => gpf.recentUploads(s)", since)
        free_names: dict[str, int] = {}
        for r in recent:
            if r.get("name") and r.get("takesUpSpace") is False:
                free_names[r["name"].lower()] = free_names.get(r["name"].lower(), 0) + 1
        old = page.evaluate("ks => gpf.spaceInfo(ks)", [m["mediaKey"] for m in man])

        ready, missing, already = [], [], []
        for m in man:
            o = old.get(m["mediaKey"])
            if not o or not o.get("takesUpSpace"):
                already.append(m)          # already trashed / no longer counts
            elif free_names.get(m["file"].lower(), 0) > 0:
                free_names[m["file"].lower()] -= 1
                ready.append(m)
            else:
                missing.append(m)
        print(f"  ready to trash: {len(ready)} (new free copy found)")
        trash.last = {"ready": len(ready), "missing": len(missing), "already": len(already)}
        print(f"  waiting: {len(missing)} (no new copy found yet - NOT touched)")
        print(f"  nothing to do: {len(already)} (already trashed or no longer counts)")
        if missing[:5]:
            print("  e.g. waiting:", ", ".join(m["file"] for m in missing[:5]))
        if not ready:
            return 0 if not missing else 2

        review = os.path.join(work, f"review-{batch}.html")
        _write_review(ready, review)
        mb = sum(int(m.get("spaceTaken") or 0) for m in ready) / 1e6
        print(f"\nReview: {os.path.abspath(review)}")
        print(f"{len(ready)} old copies ({mb:.0f} MB) will be moved to Trash (not deleted forever).")
        if not yes and input(f"Type DELETE {len(ready)} to continue: ").strip() != f"DELETE {len(ready)}":
            print("Cancelled. Nothing was trashed.")
            return 1
        n = page.evaluate("dks => gpf.trash(dks)", [m["dedupKey"] for m in ready])
        after = page.evaluate("ks => gpf.spaceInfo(ks)", [m["mediaKey"] for m in ready])
        left = sum(1 for m in ready if after.get(m["mediaKey"], {}).get("takesUpSpace"))
        with open(os.path.join(work, f"trash-log-{batch}.json"), "a", encoding="utf-8") as f:
            f.write(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "trashed": [m["mediaKey"] for m in ready]}) + "\n")
    print(f"Moved {n} to Trash. Still taking space afterwards: {left}.")
    print("Empty Trash in Google Photos when you're happy, to free the space.")
    return 0


def _write_review(items: list[dict], path: str):
    rows = "".join(f"<tr><td>{i}</td><td><a href='{html.escape(m['url'])}' target=_blank>{html.escape(m['name'])}</a></td>"
                   f"<td>{m.get('taken','')[:10]}</td><td>{m.get('kind','')}</td>"
                   f"<td class=n>{int(m.get('spaceTaken') or 0)/1e6:.2f}</td></tr>" for i, m in enumerate(items, 1))
    with open(path, "w", encoding="utf-8") as f:
        f.write("<!doctype html><meta charset=utf-8><title>Review: old copies to trash</title>"
                "<style>body{font:15px system-ui;margin:24px;max-width:900px}td,th{padding:4px 10px;text-align:left}"
                "tr:nth-child(even){background:#f3f3f3}.n{text-align:right}</style>"
                f"<h1>{len(items)} old copies to move to Trash</h1><p>Each has a new free copy already uploaded. "
                "Links open the <b>old</b> copy.</p><table><tr><th>#</th><th>Name</th><th>Taken</th><th>Kind</th>"
                f"<th class=n>MB</th></tr>{rows}</table>")
