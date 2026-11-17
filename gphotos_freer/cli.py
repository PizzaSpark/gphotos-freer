"""gphotos-freer command line."""
from __future__ import annotations

import argparse
import os
import sys

from . import audit, takeout


def cmd_audit(args) -> int:
    zips = takeout.find_zips(args.takeout)
    if not zips:
        print(f"No .zip files found in {args.takeout}")
        return 1
    print(f"Reading {len(zips)} zip file(s)...")
    items, unused, problems = takeout.read_takeout(zips, progress=lambda z: print(f"  read {z}"))
    items, dupes = audit.dedupe(items)
    no_meta = sum(1 for i in items if i.sidecar is None)
    print(f"{len(items)} unique items ({dupes} album duplicates skipped), {no_meta} without metadata, "
          f"{len(unused)} metadata files with no matching photo")
    for p in problems[:20]:
        print("  problem:", p)

    print("Checking which items use storage (reads the start of each old file)...")
    rows = audit.run_audit(items, progress=lambda i, n: print(f"  {i}/{n}", end="\r"))
    s = audit.summarize(rows)
    print()
    print(f"  Uses storage : {s['counts']['items']:6} items  {s['counts']['bytes']/1e9:7.2f} GB")
    print(f"  Unsure       : {s['check']['items']:6} items  {s['check']['bytes']/1e9:7.2f} GB")
    print(f"  Free         : {s['free']['items']:6} items  {s['free']['bytes']/1e9:7.2f} GB")
    csv_path, html_path = audit.write_reports(rows, args.out)
    print(f"\nReports:\n  {csv_path}\n  {html_path}")
    if unused:
        print("\nMetadata with no photo (file may be missing from the export):")
        for sc in unused[:20]:
            print("  ", sc.path)
    print("\nCompare 'Uses storage' (+ some of 'Unsure') with the Google Photos number at "
          "https://one.google.com/storage")
    return 0


def cmd_prepare(args) -> int:
    from . import prepare
    rows = prepare.load_rows(os.path.join(args.work, "audit.csv"), args.status, args.shared_only)
    if not rows:
        print("Nothing selected.")
        return 1
    out = os.path.join(args.work, "staged", args.batch)
    print(f"Preparing {len(rows)} items into {out} ...")
    m = prepare.prepare(rows, out, progress=lambda i, n: print(f"  {i}/{n}", end="\r"))
    print(f"\nDone: {len(m)} files, {sum(x['size'] for x in m)/1e6:.0f} MB, "
          f"date written into {sum(x['wrote_date'] for x in m)} of them.")
    return 0


def cmd_push(args) -> int:
    from . import device
    folder = os.path.join(args.work, "staged", args.batch)
    files = sorted(os.path.join(folder, f) for f in os.listdir(folder) if f != "manifest.json")
    device.prepare_device(args.serial)
    print(f"Pushing {len(files)} files to the emulator...")
    device.push(files, args.serial, progress=lambda i, n: print(f"  {i}/{n}", end="\r"))
    print("\nWaiting for Google Photos to finish backing up...")
    ok = device.wait_for_backup(args.serial)
    print("Backup complete." if ok else "Timed out waiting for backup; check the emulator.")
    return 0 if ok else 2


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="gphotos-freer",
                                description="Move Google Photos items onto free storage.")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("audit", help="Find which items in a Takeout use storage")
    a.add_argument("takeout", help="Folder with Takeout .zip files (or one zip)")
    a.add_argument("--out", default="work", help="Where to write reports (default: ./work)")
    a.set_defaults(func=cmd_audit)

    pr = sub.add_parser("prepare", help="Copy selected items out of the zips and fix their metadata")
    pr.add_argument("--work", default="work")
    pr.add_argument("--batch", required=True, help="Name for this batch, e.g. shared-2019")
    pr.add_argument("--status", choices=["counts", "check", "free"], default="counts")
    pr.add_argument("--shared-only", action="store_true", help="Only items saved from shared albums")
    pr.set_defaults(func=cmd_prepare)

    pu = sub.add_parser("push", help="Send a prepared batch to the emulator and wait for backup")
    pu.add_argument("--work", default="work")
    pu.add_argument("--batch", required=True)
    pu.add_argument("--serial", default=None, help="adb device (default: the only one)")
    pu.set_defaults(func=cmd_push)
    d = sub.add_parser("delete", help="Move a batch's old copies to Trash (after you review them)")
    d.add_argument("--work", default="work")
    d.add_argument("--batch", required=True)
    d.add_argument("--dry-run", action="store_true", help="Only report what would be deleted")
    d.add_argument("--limit", type=int, default=None, help="Only process the first N items")
    d.add_argument("--yes", action="store_true", help="Skip the typed confirmation")
    d.set_defaults(func=lambda a: __import__("gphotos_freer.deleter", fromlist=["run"]).run(
        a.work, a.batch, yes=a.yes, dry_run=a.dry_run, limit=a.limit))

    from . import web
    s = sub.add_parser("scan", help="Ask Google which items take up storage (no Takeout needed)")
    s.add_argument("--work", default="work")
    s.set_defaults(func=lambda a: (web.scan(a.work), 0)[1])

    dl = sub.add_parser("download", help="Download the originals of items that take up space")
    dl.add_argument("--work", default="work")
    dl.add_argument("--batch", required=True)
    dl.add_argument("--which", choices=["all", "shared", "own", "video"], default="all")
    dl.add_argument("--limit", type=int, default=None)
    dl.set_defaults(func=lambda a: web.download(a.work, a.batch, a.which, a.limit))

    t = sub.add_parser("trash", help="Check new copies arrived, then move old copies to Trash")
    t.add_argument("--work", default="work")
    t.add_argument("--batch", required=True)
    t.add_argument("--since-hours", type=float, default=48, help="How far back to look for the new uploads")
    t.add_argument("--yes", action="store_true", help="Skip the typed confirmation")
    t.set_defaults(func=lambda a: web.trash(a.work, a.batch, a.since_hours, a.yes))

    r = sub.add_parser("run", help="Do everything in chunks: scan, download, upload, check, trash, repeat")
    r.add_argument("--work", default="work")
    r.add_argument("--chunk-gb", type=float, default=10, help="Max size per round (default 10)")
    r.add_argument("--which", choices=["all", "shared", "own", "video"], default="all")
    r.add_argument("--rounds", type=int, default=None, help="Stop after this many rounds")
    r.add_argument("--yes", action="store_true", help="Don't ask before trashing each round")
    r.add_argument("--serial", default=None)
    r.set_defaults(func=lambda a: __import__("gphotos_freer.pipeline", fromlist=["run"]).run(
        a.work, a.chunk_gb, a.which, a.yes, a.serial, a.rounds))

    up = sub.add_parser("upload", help="Upload photos/videos from this PC for free (through the emulator)")
    up.add_argument("paths", nargs="+", help="Files or folders")
    up.add_argument("--work", default="work")
    up.add_argument("--serial", default=None)
    up.add_argument("--keep-on-emulator", action="store_true", help="Don't delete the copies from the emulator after backup")
    up.set_defaults(func=lambda a: __import__("gphotos_freer.inbox", fromlist=["upload"]).upload(
        a.paths, a.work, a.serial, a.keep_on_emulator))

    au = sub.add_parser("auth", help="Capture the sign-in token from the emulator for direct PC uploads (one time)")
    au.add_argument("--work", default="work")
    au.add_argument("--serial", default=None)
    au.add_argument("--minutes", type=float, default=15)
    au.set_defaults(func=lambda a: __import__("gphotos_freer.auth", fromlist=["capture"]).capture(a.work, a.serial, a.minutes))

    ck = sub.add_parser("check", help="Test whether uploads are still free (uploads one tiny test image)")
    ck.add_argument("--work", default="work")
    ck.add_argument("--keep", action="store_true", help="Don't move the test image to Trash afterwards")
    ck.set_defaults(func=lambda a: __import__("gphotos_freer.check", fromlist=["check"]).check(a.work, a.keep))

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
