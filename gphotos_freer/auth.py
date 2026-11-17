"""`auth`: capture the Google Photos sign-in token from the emulator, for direct PC uploads.

ReVanced GmsCore logs the Photos sign-in request. This watches the emulator's log for it and
writes the token to <work>/gp_auth.txt. The token is never printed: treat that file like a
password (it's in .gitignore; never share it).
"""
from __future__ import annotations

import os
import re
import subprocess
import time

MARK = "auth%2Fphotos.native"
_TOKEN = re.compile(r"(androidId=\S+)")


def auth_path(work: str) -> str:
    return os.path.join(work, "gp_auth.txt")


def load(work: str) -> str | None:
    p = auth_path(work)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return f.read().strip() or None
    return os.environ.get("GP_AUTH_DATA")


def capture(work: str, serial: str | None = None, timeout_min: float = 15) -> int:
    os.makedirs(work, exist_ok=True)
    from .device import adb_path
    base = [adb_path()] + (["-s", serial] if serial else [])
    # Already in the log from an earlier sign-in?
    dump = subprocess.run(base + ["logcat", "-d", "-v", "raw"], capture_output=True, text=True,
                          encoding="utf-8", errors="replace").stdout
    for line in reversed(dump.splitlines()):
        m = _TOKEN.search(line) if MARK in line else None
        if m:
            with open(auth_path(work), "w", encoding="utf-8") as f:
                f.write(m.group(1).strip())
            print(f"Found it in the emulator's log. Saved to {os.path.abspath(auth_path(work))} (keep this file private).")
            return 0
    print("Watching the emulator for the Google Photos sign-in...")
    print("If nothing appears within a minute: in the emulator, open the 'microG Settings' app ->")
    print("Accounts -> remove your account, then open Photos and sign in again (you type it yourself).")
    proc = subprocess.Popen(base + ["logcat", "-v", "raw"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            text=True, encoding="utf-8", errors="replace")
    end = time.time() + timeout_min * 60
    try:
        for line in proc.stdout:
            if MARK in line:
                m = _TOKEN.search(line)
                if m:
                    with open(auth_path(work), "w", encoding="utf-8") as f:
                        f.write(m.group(1).strip())
                    print(f"Got it. Saved to {os.path.abspath(auth_path(work))} (keep this file private).")
                    return 0
            if time.time() > end:
                break
    finally:
        proc.kill()
    print("Timed out without seeing the sign-in. Try removing and re-adding the account as described.")
    return 2
