"""Upload a folder of prepared files for free.

Uses gpmc (Google Photos' mobile upload protocol, straight from the PC) when a sign-in token
exists; otherwise falls back to the Android emulator.
"""
from __future__ import annotations

import json
import os

from . import auth, device


def upload_folder(folder: str, work: str, serial: str | None = None, threads: int = 6) -> bool:
    files = sorted(os.path.join(folder, f) for f in os.listdir(folder) if f != "manifest.json")
    token = auth.load(work)
    if token:
        from gpmc import Client
        print(f"Uploading {len(files)} files directly from this PC ({threads} at a time)...")
        out = Client(auth_data=token).upload(target=folder, threads=threads, show_progress=True,
                                             filter_exp="manifest.json", filter_exclude=True)
        with open(os.path.join(folder, "..", os.path.basename(folder) + "-upload.json"), "w", encoding="utf-8") as f:
            json.dump(out, f)
        print(f"Uploaded {len(out)} of {len(files)}.")
        return len(out) >= len(files)
    print("No sign-in token (run `gphotos-freer auth`), so using the emulator (slower).")
    device.prepare_device(serial)
    device.push(files, serial)
    return device.wait_for_backup(serial)
