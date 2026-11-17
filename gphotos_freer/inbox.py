"""`upload`: send photos/videos from the PC to Google Photos for free, through the emulator.

Your files are never changed: they're copied to a staging folder first. In the copy, a missing
date is filled in from the file name (e.g. Screenshot_20200512-130051.jpg) so it lands on the
right day in Google Photos.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import shutil
import time

from . import device
from .prepare import ExifTool
from .takeout import MEDIA_EXTS, VIDEO_EXTS

_PATTERNS = [
    # 20190915_075026, IMG_20250107_154557, VID_20241228_190846, Screenshot_20200512-130051, PXL_20230101_120000
    re.compile(r"(?<!\d)((?:19|20)\d{2})[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])[-_ T.]?([01]\d|2[0-3])[-_.:]?([0-5]\d)[-_.:]?([0-5]\d)(?!\d)"),
    # date only: 2020-05-12, 20200512
    re.compile(r"(?<!\d)((?:19|20)\d{2})[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])(?!\d)"),
]
_EPOCH_MS = re.compile(r"(?:FB_IMG|received|IMG)_(1[3-9]\d{11})(?!\d)")  # FB_IMG_1735052659874


def date_from_name(name: str) -> dt.datetime | None:
    """Best-effort local date/time from a file name, or None."""
    m = _EPOCH_MS.search(name)
    if m:
        return dt.datetime.fromtimestamp(int(m.group(1)) / 1000)
    for rx in _PATTERNS:
        m = rx.search(name)
        if m:
            parts = [int(g) for g in m.groups()] + [0, 0, 0]
            try:
                return dt.datetime(*parts[:6])
            except ValueError:
                continue
    return None


def collect(paths: list[str]) -> list[str]:
    files = []
    for p in paths:
        if os.path.isdir(p):
            for root, _, names in os.walk(p):
                files += [os.path.join(root, n) for n in sorted(names)]
        elif os.path.isfile(p):
            files.append(p)
    return [f for f in files if os.path.splitext(f)[1].lower() in MEDIA_EXTS]


def upload(paths: list[str], work: str = "work", serial: str | None = None, keep_on_emulator: bool = False) -> int:
    from .check import check
    print("Safety check: is uploading still free?")
    if check(work) != 0:
        print("Stopping. Nothing was uploaded.")
        return 1
    files = collect(paths)
    if not files:
        print("No photos or videos found.")
        return 1
    size = sum(os.path.getsize(f) for f in files)
    batch = time.strftime("inbox-%Y%m%d-%H%M%S")
    out = os.path.join(work, "staged", batch)
    os.makedirs(out, exist_ok=True)
    print(f"Preparing {len(files)} files ({size/1e6:.0f} MB). Your originals are not changed.")

    et = ExifTool()
    staged, dated = [], 0
    try:
        for f in files:
            dst = os.path.join(out, os.path.basename(f))
            base, ext = os.path.splitext(dst)
            k = 1
            while os.path.exists(dst):
                dst = f"{base}_{k}{ext}"
                k += 1
            shutil.copy2(f, dst)
            is_video = ext.lower() in VIDEO_EXTS
            tag = "-QuickTime:CreateDate" if is_video else "-DateTimeOriginal"
            has = et.run("-s3", tag, dst).strip()
            if not has or has.startswith("0000"):
                when = date_from_name(os.path.basename(f))
                if when:
                    if is_video:  # QuickTime dates are stored in UTC
                        u = when.astimezone(dt.timezone.utc).strftime("%Y:%m:%d %H:%M:%S")
                        et.run("-overwrite_original", "-m", f"-QuickTime:CreateDate={u}", dst)
                    else:
                        et.run("-overwrite_original", "-m", f"-DateTimeOriginal={when.strftime('%Y:%m:%d %H:%M:%S')}", dst)
                    os.utime(dst, (when.timestamp(), when.timestamp()))
                    dated += 1
            staged.append(dst)
    finally:
        et.close()
    if dated:
        print(f"Filled in the date from the file name for {dated} file(s).")

    from . import auth
    from .uploader import upload_folder
    if not upload_folder(out, work, serial):
        print(f"Upload didn't fully finish. Staged copies kept in: {out}")
        return 2
    print("Backed up for free.")
    if not auth.load(work) and not keep_on_emulator:
        names = [os.path.basename(s) for s in staged]
        for s in range(0, len(names), 50):
            quoted = " ".join("'" + device.CAMERA_DIR + "/" + n.replace("'", "'\\''") + "'" for n in names[s:s + 50])
            device.adb("shell", "rm -f " + quoted, serial=serial, check=False)
    shutil.rmtree(out, ignore_errors=True)
    print("Done. Your files on the PC are untouched.")
    return 0
