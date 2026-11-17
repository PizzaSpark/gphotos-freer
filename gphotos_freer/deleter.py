"""Move old copies that use storage to Google Photos' Trash, after the user reviews the list.

How it works:
- Chrome is started normally with its own profile in <work>/browser-profile and a debugging
  port. The user signs in to Google by hand in that window (never automated). Playwright then
  attaches to it.
- For each old copy: open its page, open the info panel, and only continue if Google says
  "Backed up (<size>)" and not "doesn't take up space". After clicking Move to trash, it only
  confirms if Google's dialog says "You will recover <size> from your Google Account storage".
- Everything goes to Trash. This tool never empties Trash.
- Every result is appended to <work>/delete-log-<batch>.csv, and a re-run skips items already
  deleted, so it can be stopped and resumed.
"""
from __future__ import annotations

import csv
import html
import json
import os
import re
import socket
import subprocess
import time

CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
]
PORT = 9333
FIELDS = ["time", "id", "name", "action", "reason", "recovered"]


def find_chrome() -> str:
    for p in CHROME_PATHS:
        if os.path.exists(p):
            return p
    raise SystemExit("Chrome or Edge not found.")


def _port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def start_chrome(profile_dir: str) -> None:
    if _port_open(PORT):
        return
    profile_dir = os.path.abspath(profile_dir)  # Chrome ignores relative profile paths
    os.makedirs(profile_dir, exist_ok=True)
    subprocess.Popen([find_chrome(), f"--remote-debugging-port={PORT}", f"--user-data-dir={profile_dir}",
                      "--no-first-run", "--no-default-browser-check", "--window-size=1280,900",
                      "https://photos.google.com/"])
    for _ in range(60):
        if _port_open(PORT):
            return
        time.sleep(0.5)
    raise SystemExit("Chrome did not start.")


def load_batch(work: str, batch: str) -> list[dict]:
    with open(os.path.join(work, "staged", batch, "manifest.json"), encoding="utf-8") as f:
        m = json.load(f)
    out = []
    for x in m:
        url = x.get("url", "")
        if "/photo/" in url:
            out.append({"id": url.rsplit("/", 1)[1], "url": url, "name": x["name"],
                        "taken": x.get("taken", ""), "size": x.get("size", 0)})
    return out


def read_log(path: str) -> dict[str, dict]:
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return {r["id"]: r for r in csv.DictReader(f)}


def write_review(items: list[dict], path: str, batch: str) -> None:
    rows = "".join(
        f"<tr><td>{i}</td><td><a href='{html.escape(x['url'])}' target=_blank>{html.escape(x['name'])}</a></td>"
        f"<td>{x['taken'][:10]}</td><td class=n>{int(x['size'])/1e6:.2f}</td></tr>"
        for i, x in enumerate(items, 1))
    total = sum(int(x["size"]) for x in items) / 1e6
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"""<!doctype html><meta charset=utf-8><title>Review deletion: {html.escape(batch)}</title>
<style>body{{font:15px system-ui;margin:24px;max-width:900px}}td,th{{padding:4px 10px;text-align:left}}
tr:nth-child(even){{background:#f3f3f3}}.n{{text-align:right}}</style>
<h1>{len(items)} old copies will be moved to Trash</h1>
<p>About {total:.0f} MB. Each one already has a new free copy uploaded through the emulator.
Each link opens the <b>old</b> copy. The tool only deletes a copy if Google says it takes up
storage, and never empties Trash.</p>
<table><tr><th>#</th><th>Name</th><th>Taken</th><th class=n>MB</th></tr>{rows}</table>""")


def _info_text(page) -> str:
    return page.evaluate("document.body.innerText")


def trash_one(page, item: dict) -> dict:
    page.goto(item["url"], wait_until="domcontentloaded")
    here = lambda: page.url.split("?")[0].rstrip("/").endswith("/photo/" + item["id"])
    if not here():
        return {"action": "skip", "reason": "page did not open (already deleted?)"}
    txt = ""
    for attempt in range(25):
        txt = _info_text(page)
        if re.search(r"Backed up \(|take up space", txt):
            break
        if attempt in (3, 10, 18):  # info panel closed: toggle it with the 'i' shortcut
            page.keyboard.press("i")
        time.sleep(0.4)
    if "/trash/" in page.url or re.search(r"\bRestore\b", txt) and "Move to trash" not in txt:
        return {"action": "skip", "reason": "already in trash"}
    backed = re.search(r"Backed up \(([^)]*)\)", txt)
    if re.search(r"doesn.t take up space", txt):
        return {"action": "skip", "reason": "free (doesn't take up space)"}
    if not backed:
        return {"action": "skip", "reason": "no storage line in info panel"}
    if not here():
        return {"action": "skip", "reason": "moved away"}

    btn = page.locator('button[aria-label="Move to trash"]:visible').last
    if btn.count() == 0:
        page.keyboard.press("Shift+3")
    else:
        btn.click()
    dialog = page.locator('[role="dialog"]:has-text("Move to trash"), [role="alertdialog"]:has-text("Move to trash")').last
    try:
        dialog.wait_for(state="visible", timeout=5000)
    except Exception:
        return {"action": "skip", "reason": "no delete dialog"}
    dtext = dialog.inner_text()
    rec = re.search(r"recover ([\d.,]+ ?[KMG]?B)", dtext)
    if not rec or not here():
        dialog.get_by_role("button", name="Cancel").click()
        return {"action": "skip", "reason": "dialog did not say storage would be recovered" if not rec else "moved away"}
    dialog.get_by_role("button", name="Move to trash").click()
    dialog.wait_for(state="hidden", timeout=10000)
    return {"action": "deleted", "reason": "", "recovered": rec.group(1)}


def _signed_in(ctx) -> bool:
    names = {c["name"] for c in ctx.cookies("https://photos.google.com")}
    return bool(names & {"SID", "__Secure-1PSID", "__Secure-3PSID"})


def wait_for_sign_in(ctx, page) -> None:
    """Wait until the tool's Chrome window is signed in to Google. Never types anything itself."""
    if not _signed_in(ctx):
        page.goto("https://accounts.google.com/ServiceLogin?continue=https://photos.google.com/",
                  wait_until="domcontentloaded")
        print("\nSign in to Google in the tool's Chrome window. Waiting (this can take a while)...")
        while not _signed_in(ctx):
            time.sleep(2)
    for attempt in range(3):
        try:
            page.goto("https://photos.google.com/", wait_until="commit", timeout=120000)
            break
        except Exception:
            if attempt == 2:
                raise
            time.sleep(5)
    for _ in range(30):
        if "photos.google.com" in page.url and "/about" not in page.url:
            print("Signed in.")
            return
        time.sleep(1)
    raise SystemExit("Signed in, but Google Photos didn't open. Check the Chrome window.")


def run(work: str, batch: str, yes: bool = False, dry_run: bool = False, limit: int | None = None) -> int:
    from playwright.sync_api import sync_playwright

    items = load_batch(work, batch)
    log_path = os.path.join(work, f"delete-log-{batch}.csv")
    done = {k for k, v in read_log(log_path).items() if v["action"] == "deleted"}
    todo = [x for x in items if x["id"] not in done]
    if limit:
        todo = todo[:limit]
    if not todo:
        print("Nothing left to delete in this batch.")
        return 0

    review = os.path.join(work, f"review-{batch}.html")
    write_review(todo, review, batch)
    print(f"{len(todo)} old copies to move to Trash ({len(done)} already done).")
    print(f"Review the list first: {os.path.abspath(review)}")
    if not dry_run and not yes:
        answer = input(f"Type DELETE {len(todo)} to continue, anything else cancels: ").strip()
        if answer != f"DELETE {len(todo)}":
            print("Cancelled. Nothing was deleted.")
            return 1

    print("\nA separate Chrome window will open for this (its own profile, not your normal Chrome).")
    start_chrome(os.path.join(work, "browser-profile"))
    new_log = not os.path.exists(log_path)
    with sync_playwright() as p, open(log_path, "a", newline="", encoding="utf-8") as lf:
        w = csv.DictWriter(lf, fieldnames=FIELDS)
        if new_log:
            w.writeheader()
        browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}")
        ctx = browser.contexts[0]
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.set_default_timeout(15000)
        wait_for_sign_in(ctx, page)
        counts = {"deleted": 0, "skip": 0}
        for n, item in enumerate(todo, 1):
            if dry_run:
                page.goto(item["url"], wait_until="domcontentloaded")
                time.sleep(1.5)
                txt = _info_text(page)
                if not re.search(r"Backed up \(|take up space", txt):
                    page.keyboard.press("i")
                    time.sleep(1.5)
                    txt = _info_text(page)
                b = re.search(r"Backed up \(([^)]*)\)", txt)
                r = {"action": "dry-run", "reason": "free" if re.search(r"doesn.t take up space", txt)
                     else (f"would delete ({b.group(1)})" if b else "no storage line")}
            else:
                try:
                    r = trash_one(page, item)
                except Exception as e:
                    r = {"action": "skip", "reason": f"error: {e.__class__.__name__}: {str(e)[:120]}"}
            counts[r["action"]] = counts.get(r["action"], 0) + 1
            w.writerow({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "id": item["id"], "name": item["name"],
                        "action": r["action"], "reason": r.get("reason", ""), "recovered": r.get("recovered", "")})
            lf.flush()
            print(f"  {n}/{len(todo)}  {r['action']:8} {item['name']}  {r.get('recovered') or r.get('reason', '')}")
        browser.close()  # detaches; Chrome window stays open
    print(f"\nDone: {counts}. Log: {os.path.abspath(log_path)}")
    if dry_run:
        print("Dry run: nothing was deleted.")
    else:
        print("Deleted items are in Trash. Check them, then empty Trash in Google Photos to free the space.")
    return 0
