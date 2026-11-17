"""`run`: free up space in chunks.

Each round: scan -> download up to N GB -> upload through the emulator -> check the new
copies arrived -> move the old copies to Trash -> delete the chunk from the PC and the
emulator -> next round. Stops at the first sign of trouble, without trashing anything
it couldn't verify.
"""
from __future__ import annotations

import json
import os
import shutil
import time

from . import device, web


def _free_gb(path: str) -> float:
    return shutil.disk_usage(os.path.abspath(path)).free / 1e9


def _cleanup(work: str, batch: str, serial: str | None):
    from . import auth
    folder = os.path.join(work, "staged", batch)
    files = [f for f in os.listdir(folder) if f != "manifest.json"]
    if auth.load(work):  # uploaded directly from the PC: nothing on the emulator
        for f in files:
            os.remove(os.path.join(folder, f))
        return
    # Remove from the emulator (the cloud copies stay; only the local files go).
    for s in range(0, len(files), 50):
        quoted = " ".join("'" + device.CAMERA_DIR + "/" + f.replace("'", "'\''") + "'" for f in files[s:s + 50])
        device.adb("shell", "rm -f " + quoted, serial=serial, check=False)
    device.adb("shell", "content", "call", "--uri", "content://media", "--method", "scan_volume",
               "--arg", "external_primary", serial=serial, check=False)
    for f in files:
        os.remove(os.path.join(folder, f))


def run(work: str, chunk_gb: float | None = 10, which: str = "all", yes: bool = False,
        serial: str | None = None, max_rounds: int | None = None) -> int:
    """chunk_gb None or 0 = everything in one round."""
    chunk_gb = chunk_gb or None
    from .check import check
    print("Safety check: is uploading still free?")
    if check(work) != 0:
        print("Stopping before doing anything. Nothing was downloaded, uploaded or trashed.")
        return 1
    state_path = os.path.join(work, "run-state.json")
    state = json.load(open(state_path)) if os.path.exists(state_path) else {"done": [], "rounds": 0}
    rnd = 0
    while max_rounds is None or rnd < max_rounds:
        rnd += 1
        n = state["rounds"] + 1
        print(f"\n=== Round {n}: " + (f"up to {chunk_gb:g} GB ===" if chunk_gb else "everything ==="))
        found = web.scan(work)
        left = [x for x in found if which == "all" or web._kind(x) == which]
        if not left:
            print("Nothing left that takes up space. All done!")
            return 0
        total = sum(int(x.get("size") or 0) for x in left) / 1e9
        need = (min(chunk_gb, total) if chunk_gb else total) * 1.2
        if _free_gb(work) < need:
            print(f"Not enough free disk space: need ~{need:.1f} GB, have {_free_gb(work):.1f} GB. "
                  f"Use rounds (--chunk-gb 10) or a smaller --chunk-gb, or free some space.")
            return 3
        batch = f"chunk-{n:03d}"
        started = time.time()
        web.download(work, batch, which, max_bytes=int(chunk_gb * 1e9) if chunk_gb else None)
        manifest = json.load(open(os.path.join(work, "staged", batch, "manifest.json"), encoding="utf-8"))
        if not manifest:
            print("Nothing could be downloaded this round; stopping.")
            return 2

        folder = os.path.join(work, "staged", batch)
        from .uploader import upload_folder
        if not upload_folder(folder, work, serial):
            print("Upload didn't fully finish. Nothing trashed; re-run to continue.")
            return 2

        hours = (time.time() - started) / 3600 + 1
        code = web.trash(work, batch, since_hours=hours, yes=yes)
        res = getattr(web.trash, "last", {})
        if code != 0 or res.get("missing"):
            print("Some new copies weren't found yet, so this round stopped before cleanup. "
                  "Wait a bit and run `trash --batch " + batch + "` again.")
            return 2
        _cleanup(work, batch, serial)
        state["done"] += [m["mediaKey"] for m in manifest]
        state["rounds"] = n
        json.dump(state, open(state_path, "w"))
        print(f"Round {n} done: {len(manifest)} items moved onto free storage.")
    return 0
