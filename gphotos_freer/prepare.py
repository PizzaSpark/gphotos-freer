"""Copy selected items out of the Takeout zips into a staging folder, ready for upload.

For each file:
- the date taken is written into the file if it's missing (from the Takeout JSON),
- GPS is written if the file has none and the JSON has a real location,
- a small XMP tag is added so Google doesn't skip it as an exact duplicate,
- the file's modified time is set to the date taken.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import os
import subprocess
import zipfile

MARKER = "gphotos-freer"


class ExifTool:
    """One long-running exiftool process (-stay_open), much faster than one process per file."""

    def __init__(self, exe: str = "exiftool"):
        self.p = subprocess.Popen(
            [exe, "-stay_open", "True", "-@", "-", "-common_args", "-charset", "filename=utf8"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)  # stderr unread would deadlock

    def run(self, *args: str) -> str:
        cmd = "\n".join(args) + "\n-execute\n"
        self.p.stdin.write(cmd.encode("utf-8"))
        self.p.stdin.flush()
        out = b""
        while not out.endswith(b"{ready}\r\n") and not out.endswith(b"{ready}\n"):
            chunk = self.p.stdout.readline()
            if not chunk:
                break
            out += chunk
        return out.decode("utf-8", "replace").rsplit("{ready}", 1)[0]

    def close(self):
        try:
            self.p.stdin.write(b"-stay_open\nFalse\n")
            self.p.stdin.flush()
            self.p.wait(10)
        except Exception:
            self.p.kill()


def _unique_path(folder: str, name: str) -> str:
    base, ext = os.path.splitext(name)
    path, n = os.path.join(folder, name), 1
    while os.path.exists(path):
        path = os.path.join(folder, f"{base}_{n}{ext}")
        n += 1
    return path


def prepare(rows: list[dict], out_dir: str, tz: dt.tzinfo | None = None, progress=None) -> list[dict]:
    """Extract and fix each row (rows come from audit.csv). Returns manifest entries."""
    os.makedirs(out_dir, exist_ok=True)
    tz = tz or dt.datetime.now().astimezone().tzinfo
    et = ExifTool()
    manifest = []
    try:
        for n, r in enumerate(rows, 1):
            path = _unique_path(out_dir, r["name"])
            with zipfile.ZipFile(r["zip"]) as zf, open(path, "wb") as f:
                f.write(zf.read(r["member"]))

            existing = json.loads(et.run("-j", "-n", "-DateTimeOriginal", "-CreateDate",
                                         "-GPSLatitude", "-GPSLongitude", path) or "[{}]")[0]
            args = ["-overwrite_original", "-m", f"-XMP-xmp:CreatorTool={MARKER}"]
            taken = dt.datetime.fromisoformat(r["taken"]) if r.get("taken") else None
            is_video = os.path.splitext(path)[1].lower() in (".mp4", ".mov", ".m4v", ".3gp")
            wrote_date = False
            if taken and not existing.get("DateTimeOriginal") and not (is_video and existing.get("CreateDate")):
                local = taken.astimezone(tz)
                if is_video:  # QuickTime dates are UTC by spec
                    args += [f"-QuickTime:CreateDate={taken.strftime('%Y:%m:%d %H:%M:%S')}",
                             f"-QuickTime:ModifyDate={taken.strftime('%Y:%m:%d %H:%M:%S')}"]
                else:
                    args += [f"-DateTimeOriginal={local.strftime('%Y:%m:%d %H:%M:%S')}",
                             f"-OffsetTimeOriginal={local.strftime('%z')[:3]}:{local.strftime('%z')[3:]}"]
                wrote_date = True
            lat, lon = float(r.get("lat") or 0), float(r.get("lon") or 0)
            if (lat or lon) and not existing.get("GPSLatitude"):
                args += [f"-GPSLatitude={abs(lat)}", f"-GPSLatitudeRef={'N' if lat >= 0 else 'S'}",
                         f"-GPSLongitude={abs(lon)}", f"-GPSLongitudeRef={'E' if lon >= 0 else 'W'}"]
            et.run(*args, path)
            if taken:
                ts = taken.timestamp()
                os.utime(path, (ts, ts))
            manifest.append({"file": os.path.basename(path), "name": r["name"], "url": r.get("url", ""),
                             "taken": r.get("taken", ""), "size": os.path.getsize(path),
                             "wrote_date": wrote_date, "old_status": r.get("status", "")})
            if progress:
                progress(n, len(rows))
    finally:
        et.close()
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=1)
    return manifest


def load_rows(audit_csv: str, status: str | None = None, shared_only: bool = False) -> list[dict]:
    with open(audit_csv, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if status:
        rows = [r for r in rows if r["status"] == status]
    if shared_only:
        rows = [r for r in rows if r["from_shared_album"] == "True"]
    return rows
