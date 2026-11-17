"""Send prepared files to the Android emulator (or a USB phone) with adb."""
from __future__ import annotations

import os
import re
import subprocess
import time

CAMERA_DIR = "/sdcard/DCIM/Camera"
PHOTOS_PKG = "app.revanced.android.photos"
GMS_PKG = "app.revanced.android.gms"


def adb_path() -> str:
    """adb from $ADB, the SDK chosen by scripts/setup-emulator.ps1, the usual SDK folder, or PATH."""
    import shutil
    if os.environ.get("ADB"):
        return os.environ["ADB"]
    marker = os.path.join(os.path.expanduser("~"), ".gphotos-freer-sdk")
    roots = []
    if os.path.exists(marker):
        with open(marker, encoding="utf-8-sig") as f:
            roots.append(f.read().strip())
    roots.append(os.path.expandvars(r"%LOCALAPPDATA%\Android\Sdk"))
    for r in roots:
        p = os.path.join(r, "platform-tools", "adb.exe" if os.name == "nt" else "adb")
        if os.path.exists(p):
            return p
    return shutil.which("adb") or "adb"


def adb(*args: str, serial: str | None = None, check: bool = True) -> str:
    cmd = [adb_path()] + (["-s", serial] if serial else []) + list(args)
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env={**os.environ, "MSYS_NO_PATHCONV": "1"})
    if check and r.returncode != 0:
        raise RuntimeError(f"adb {' '.join(args)} failed: {r.stderr.strip() or r.stdout.strip()}")
    return r.stdout


def prepare_device(serial: str | None = None):
    """One-time fixes: let GmsCore/Photos run in the background (otherwise backup stalls)."""
    for pkg in (GMS_PKG, PHOTOS_PKG):
        adb("shell", "dumpsys", "deviceidle", "whitelist", "+" + pkg, serial=serial, check=False)
    adb("shell", "mkdir", "-p", CAMERA_DIR, serial=serial)


def push(files: list[str], serial: str | None = None, progress=None):
    for n, f in enumerate(files, 1):
        adb("push", "-a", f, CAMERA_DIR + "/", serial=serial)  # -a keeps the file time
        if progress:
            progress(n, len(files))
    adb("shell", "content", "call", "--uri", "content://media", "--method", "scan_volume",
        "--arg", "external_primary", serial=serial)


def open_photos(serial: str | None = None):
    # Never force-stop Photos: it forgets the chosen account.
    adb("shell", "monkey", "-p", PHOTOS_PKG, "-c", "android.intent.category.LAUNCHER", "1",
        serial=serial, check=False)


def screen_text(serial: str | None = None) -> list[str]:
    xml = adb("exec-out", "uiautomator", "dump", "/dev/tty", serial=serial, check=False)
    return [t for t in re.findall(r'text="([^"]+)"', xml)]


def wait_for_backup(serial: str | None = None, timeout: int = 3600, settle: int = 30, log=print) -> bool:
    """Keep Photos in front and wait until it shows 'Backup complete' for `settle` seconds."""
    open_photos(serial)
    start, done_since, last = time.time(), None, ""
    while time.time() - start < timeout:
        texts = screen_text(serial)
        status = next((t for t in texts if re.search(r"back(ing)? ?up|upload|left|complete|waiting", t, re.I)), "")
        if status != last:
            log(f"  [{int(time.time() - start):4}s] {status or '(no backup status on screen)'}")
            last = status
        if "complete" in status.lower():
            done_since = done_since or time.time()
            if time.time() - done_since >= settle:
                return True
        else:
            done_since = None
            if not status:
                open_photos(serial)
        time.sleep(5)
    return False
