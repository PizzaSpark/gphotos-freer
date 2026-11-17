"""Pick-a-number menus: plain `gphotos-freer` and `gphotos-freer setup`."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

SETUP_SCRIPT = os.path.join(os.path.dirname(__file__), "setup-android.ps1")


def choose(question: str, options: list[str], default: int = 1) -> int:
    """Show numbered options, return the picked number (0 = the last option when it's 'Back/Exit')."""
    print(f"\n{question}")
    for i, o in enumerate(options, 1):
        print(f"  [{i}] {o}")
    while True:
        a = input(f"Pick 1-{len(options)} [{default}]: ").strip()
        if not a:
            return default
        if a.isdigit() and 1 <= int(a) <= len(options):
            return int(a)
        print("  Please type one of the numbers.")


def ask(question: str, default: str = "") -> str:
    a = input(f"{question}{f' [{default}]' if default else ''}: ").strip().strip('"').strip("'")
    return a or default


def yes(question: str, default: bool = True) -> bool:
    a = input(f"{question} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
    return default if not a else a.startswith("y")


def pause():
    input("\nPress Enter to go back to the menu...")


# --- Setup -----------------------------------------------------------------------------------

def setup(work: str = "work") -> int:
    from . import auth
    print("\n=== One-time setup ===")
    print("Free uploads need a sign-in token from the patched Google Photos app on Android.")
    print("You sign in once on Android; after that everything runs from this PC.")
    if auth.load(work):
        print(f"\nYou already have a sign-in token ({os.path.abspath(auth.auth_path(work))}).")
        if not yes("Get a new one anyway?", default=False):
            return _setup_browser(work)

    pick = choose("Which Android device will you sign in on?", [
        "My own Android phone, over a USB cable  (downloads adb, ~10 MB)",
        "An emulator on this PC  (downloads ~4 GB, needs Java 17 and ~8 GB disk)",
        "It's already set up (Photos is installed and I'm signed in)",
        "Back",
    ])
    if pick == 4:
        return 0
    if pick in (1, 2):
        if os.name != "nt":
            print("The automatic device setup is Windows-only. Install adb, ReVanced GmsCore and your "
                  "patched Photos yourself, then pick option 3.")
            return 1
        print("\nYou need a ReVanced-patched Google Photos APK (this tool can't include it).")
        print("Make one with ReVanced Manager / CLI, or leave this empty if it's already installed.")
        apk = ask("Path to the patched Photos .apk (Enter to skip)")
        if apk and not os.path.isfile(apk):
            print(f"Can't find {apk}.")
            return 1
        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", SETUP_SCRIPT,
               "-Mode", "phone" if pick == 1 else "emulator"]
        if apk:
            cmd += ["-PhotosApk", os.path.abspath(apk)]
        if subprocess.run(cmd).returncode != 0:
            print("\nDevice setup stopped (see the message above). Fix that and run setup again.")
            return 1

    where = "phone" if pick == 1 else "emulator" if pick == 2 else "device"
    print(f"\nNow, on the {where}: open Photos and sign in to your Google account (type it yourself).")
    input("Press Enter once you've signed in (or while you're signing in)...")
    if auth.capture(work) != 0:
        return 1
    return _setup_browser(work)


def _setup_browser(work: str) -> int:
    if not yes("\nLast step: sign in to the tool's own Chrome window and check uploads are free?"):
        return 0
    from .check import check
    return check(work)


# --- Run -------------------------------------------------------------------------------------

def _scan_summary(work: str) -> tuple[int, float] | None:
    p = os.path.join(work, "space-scan.json")
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        found = json.load(f)["found"]
    return len(found), sum(int(x.get("size") or 0) for x in found) / 1e9


def run_menu(work: str = "work") -> int:
    summary = _scan_summary(work)
    os.makedirs(work, exist_ok=True)
    free = shutil.disk_usage(os.path.abspath(work)).free / 1e9
    if summary:
        print(f"\nLast scan: {summary[0]} items taking up {summary[1]:.1f} GB. Free disk here: {free:.0f} GB.")
    else:
        print(f"\nFree disk here: {free:.0f} GB.")
    pick = choose("How much at a time?", [
        "Everything at once  (needs free disk of about the total size + 20%)",
        "In 10 GB rounds  (safe if disk space is tight; you can stop between rounds)",
        "In rounds of a size I pick",
        "Back",
    ], default=1 if summary and free > summary[1] * 1.2 else 2)
    if pick == 4:
        return 0
    chunk = None if pick == 1 else 10.0 if pick == 2 else float(ask("Size per round in GB", "5"))
    confirm = yes("Show the review page and ask before moving old copies to Trash?")
    from .pipeline import run
    return run(work, chunk, "all", yes=not confirm)


# --- Main menu -------------------------------------------------------------------------------

def main_menu(work: str = "work") -> int:
    from . import auth
    try:
        while True:
            print("\n" + "=" * 52)
            print("  gphotos-freer: move Google Photos onto free storage")
            print("=" * 52)
            print("  Sign-in token: " + ("found" if auth.load(work) else "not set up yet (pick 1)"))
            pick = choose("What do you want to do?", [
                "Set up (first time)",
                "See what takes up space",
                "Free up space",
                "Upload photos/videos from this PC",
                "Check uploads are still free",
                "Exit",
            ], default=2 if auth.load(work) else 1)
            if pick == 6:
                return 0
            if pick == 1:
                setup(work)
            elif pick == 2:
                from . import web
                web.scan(work)
            elif pick == 3:
                run_menu(work)
            elif pick == 4:
                path = ask("Folder or file to upload (you can drag it into this window)")
                if path:
                    from .inbox import upload
                    upload([path], work)
            elif pick == 5:
                from .check import check
                check(work)
            pause()
    except (KeyboardInterrupt, EOFError):
        print("\nBye.")
        return 0
