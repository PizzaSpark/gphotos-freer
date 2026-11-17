"""Label each item 'free', 'counts' or 'check' (unsure), and write a report.

Google's rule: items uploaded in High quality / Storage saver before 2021-06-01 don't count
toward storage. Anything uploaded later, or in Original quality, counts.
"""
from __future__ import annotations

import csv
import datetime as dt
import html
import os
from concurrent.futures import ThreadPoolExecutor

from .probe import probe
from .takeout import Item

CUTOFF = dt.datetime(2021, 6, 1, tzinfo=dt.timezone.utc)
SAVER_MAX_PIXELS = 16_400_000      # Storage saver resizes photos above ~16 MP
SAVER_MAX_BPP = 1.0                # bits per pixel; Google re-encodes land far below this


def _ts(sidecar, key):
    try:
        return dt.datetime.fromtimestamp(int(sidecar.data[key]["timestamp"]), dt.timezone.utc)
    except (KeyError, TypeError, ValueError):
        return None


def classify(item: Item, info: dict | None) -> tuple[str, str]:
    sc = item.sidecar
    if sc is None:
        return "check", "no metadata file, upload date unknown"
    uploaded = _ts(sc, "creationTime")
    if uploaded is None:
        return "check", "no upload date in metadata"
    if uploaded >= CUTOFF:
        if "fromSharedAlbum" in sc.data.get("googlePhotosOrigin", {}):
            return "counts", "saved from a shared album after 1 Jun 2021"
        # Own uploads after the cutoff count, unless they came from a Pixel or a Pixel-spoofed
        # app (e.g. ReVanced Photos). Takeout metadata can't tell those apart.
        return "check", "uploaded after 1 Jun 2021 (free only if from a Pixel / patched app)"
    info = info or {}
    if item.is_video:
        if info.get("google_encoded"):
            return "free", "before Jun 2021, re-encoded by Google (Storage saver)"
        return "check", "before Jun 2021, video not re-encoded (may be Original quality)"
    w, h = info.get("width"), info.get("height")
    if not w or not h:
        return "check", "before Jun 2021, image size unreadable"
    px = w * h
    if px > SAVER_MAX_PIXELS:
        return "counts", f"before Jun 2021 but {px/1e6:.1f} MP (bigger than Storage saver allows)"
    bpp = item.size * 8 / px
    if bpp < SAVER_MAX_BPP:
        return "free", "before Jun 2021, compressed like Storage saver"
    return "check", f"before Jun 2021, {bpp:.1f} bits/pixel (may be Original quality)"


def dedupe(items: list[Item]) -> tuple[list[Item], int]:
    """Album folders repeat items from year folders; the sidecar URL identifies the Google item."""
    seen, out, dupes = set(), [], 0
    for it in items:
        key = (it.sidecar.data.get("url") if it.sidecar else None) or ("file", it.name.lower(), it.size)
        if key in seen:
            dupes += 1
            continue
        seen.add(key)
        out.append(it)
    return out, dupes


def run_audit(items: list[Item], workers: int = 8, progress=None) -> list[dict]:
    def needs_probe(it: Item) -> bool:
        up = _ts(it.sidecar, "creationTime") if it.sidecar else None
        return up is not None and up < CUTOFF

    def one(it: Item) -> dict:
        info = None
        if needs_probe(it):
            try:
                info = probe(it.zip_path, it.member, it.is_video)
            except Exception as e:  # unreadable entry -> reported as 'check'
                info = {"error": str(e)}
        status, reason = classify(it, info)
        sc = it.sidecar.data if it.sidecar else {}
        up, taken = (_ts(it.sidecar, "creationTime"), _ts(it.sidecar, "photoTakenTime")) if it.sidecar else (None, None)
        origin = sc.get("googlePhotosOrigin", {})
        return {
            "status": status,
            "reason": reason,
            "size": it.size,
            "name": it.name,
            "taken": taken.isoformat() if taken else "",
            "uploaded": up.isoformat() if up else "",
            "from_shared_album": "fromSharedAlbum" in origin,
            "url": sc.get("url", ""),
            "lat": (sc.get("geoData") or {}).get("latitude", 0),
            "lon": (sc.get("geoData") or {}).get("longitude", 0),
            "zip": it.zip_path,
            "member": it.member,
            "match_rule": it.match_rule,
        }

    rows = []
    with ThreadPoolExecutor(workers) as ex:
        for i, row in enumerate(ex.map(one, items), 1):
            rows.append(row)
            if progress and i % 200 == 0:
                progress(i, len(items))
    return rows


def summarize(rows: list[dict]) -> dict:
    s = {k: {"items": 0, "bytes": 0} for k in ("counts", "check", "free")}
    for r in rows:
        s[r["status"]]["items"] += 1
        s[r["status"]]["bytes"] += r["size"]
    return s


def write_reports(rows: list[dict], out_dir: str) -> tuple[str, str]:
    os.makedirs(out_dir, exist_ok=True)
    csv_path = os.path.join(out_dir, "audit.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["status"])
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: (r["status"], r["taken"])))

    html_path = os.path.join(out_dir, "counts-list.html")
    s = summarize(rows)
    parts = [
        "<!doctype html><meta charset='utf-8'><title>Items using storage</title>",
        "<style>body{font:14px system-ui;margin:24px;max-width:1000px}td,th{padding:4px 8px;text-align:left}"
        "tr:nth-child(even){background:#f3f3f3}.n{text-align:right}h2{margin-top:32px}</style>",
        "<h1>Items that use Google storage</h1>",
        f"<p>Counts: {s['counts']['items']} items, {s['counts']['bytes']/1e9:.2f} GB. "
        f"Unsure: {s['check']['items']} items, {s['check']['bytes']/1e9:.2f} GB. "
        f"Free: {s['free']['items']} items, {s['free']['bytes']/1e9:.2f} GB.</p>",
        "<p>Click a name to open it in Google Photos.</p>",
    ]
    for status, title in (("counts", "Uses storage"), ("check", "Unsure (check these)")):
        sel = sorted((r for r in rows if r["status"] == status), key=lambda r: r["taken"])
        if not sel:
            continue
        parts.append(f"<h2>{title} ({len(sel)})</h2><table><tr><th>Taken</th><th>Name</th>"
                     "<th class=n>MB</th><th>Why</th></tr>")
        for r in sel:
            name = html.escape(r["name"])
            link = f"<a href='{html.escape(r['url'])}' target=_blank>{name}</a>" if r["url"] else name
            parts.append(f"<tr><td>{r['taken'][:10]}</td><td>{link}</td>"
                         f"<td class=n>{r['size']/1e6:.1f}</td><td>{html.escape(r['reason'])}</td></tr>")
        parts.append("</table>")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))
    return csv_path, html_path
