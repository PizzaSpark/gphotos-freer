# gphotos-freer

Make your Google Photos library stop using your Google storage, without losing anything.

Original Pixel phones get free, unlimited Google Photos backup. A ReVanced-patched Google Photos
app makes Google think you're on one. This tool uses that to:

- **Find** every photo and video that's using your storage (it asks Google directly, so no Takeout download).
- **Move them onto free storage:** download each one, re-upload it the free way, check the new copy
  arrived, then move the old copy to Trash. Your library looks the same; it just stops counting.
- **Upload new photos and videos from your PC** for free, without your phone.
- **Check every month** whether the trick still works, and refuse to upload if it doesn't.

> [!WARNING]
> **Read this first.**
> - Pretending to be a Pixel goes against Google's terms of service. Google could close the trick
>   at any time, and in the worst case could act on your account. Use it at your own risk.
> - The tool uses Google Photos' **undocumented** website and mobile-app functions. If Google
>   changes them, the tool breaks until it's updated.
> - Re-uploaded items keep their dates and quality, but **lose album membership, favourites,
>   comments, and the link to a shared album** (for photos you saved from someone else's album).
>   Face groups are recalculated by Google.
> - Old copies are only ever moved to **Trash** (restorable for 60 days). The tool never empties
>   Trash; you do that yourself once you're happy.

## What you need

- Windows PC with **Python 3.10+** and **Google Chrome** (or Edge)
- **ExifTool**: `winget install OliverBetz.ExifTool` (or `scoop install exiftool`)
- **One time only:** an Android device running a **ReVanced-patched Google Photos** + **ReVanced GmsCore**,
  to get your sign-in token. Either:
  - your own Android phone with USB debugging on, plus [platform-tools (adb)](https://developer.android.com/tools/releases/platform-tools), or
  - the emulator from `scripts/setup-emulator.ps1` (no Android Studio needed; ~4 GB).

  You have to patch Google Photos yourself (this repo doesn't include it): use
  [ReVanced Manager](https://revanced.app) / its CLI with the Google Photos patches, or another
  ReVanced builder. Install [ReVanced GmsCore](https://github.com/ReVanced/GmsCore/releases)
  (the `-signed.apk`, not `-hw-signed`) alongside it.

## Install

```bash
git clone https://github.com/<you>/gphotos-freer
cd gphotos-freer
pip install -e .
```

That gives you the `gphotos-freer` command.

## One-time setup

**1. Get your sign-in token.**
Connect your phone (USB debugging) or start the emulator, open the patched Photos and sign in. Then:

```bash
gphotos-freer auth
```

It saves the token to `work/gp_auth.txt`. **That file is like a password for your Google account:
never share it or commit it** (it's already in `.gitignore`). If it doesn't find the token, it
tells you how to re-trigger the sign-in.

After this, the phone/emulator isn't needed anymore: uploads go straight from your PC.

**2. Sign in to the tool's browser window.**
The first command that needs it opens a **separate Chrome window** with its own profile (not your
normal Chrome). Sign in to Google there once; it remembers you.

**3. Check the trick works for your account:**

```bash
gphotos-freer check
```

`STILL FREE` = good to go. `NOT FREE ANYMORE` = stop.

## Free up your storage

See what's using space (read-only):

```bash
gphotos-freer scan
```

Then do it, in rounds of up to 10 GB (so you never need more than ~12 GB free disk):

```bash
gphotos-freer run --chunk-gb 10
```

Each round: download → upload free → check every new copy arrived → show you a review page and ask
you to type `DELETE <n>` → move the old copies to Trash → delete the round's files from your PC.
Stop any time; run it again to carry on.

When it's done, **empty Trash in Google Photos**, and check
[one.google.com/storage](https://one.google.com/storage) a few hours later.

Options: `--chunk-gb 5` (smaller rounds), `--rounds 1` (just one round),
`--which shared|own|video` (only that kind), `--yes` (don't ask before trashing each round).

## Upload new photos and videos for free

```bash
gphotos-freer upload "D:\Photos\Trip 2026"
```

Files or folders. Your files aren't changed (it works on copies). Missing dates are filled in
from file names like `IMG_20250107_154557.jpg` or `Screenshot_20200512-130051.jpg`.

## Monthly check

Run `gphotos-freer check` now and then. `run` and `upload` also run it first and refuse to
continue if uploads would count toward your storage. Results are logged in `work/check-history.jsonl`.

## Other commands

| Command | What it does |
|---|---|
| `scan` | List what takes up space (`work/space-scan.json`) |
| `download --batch NAME` | Download originals of what takes up space |
| `trash --batch NAME` | Verify new copies, then trash the old ones (with review) |
| `audit TAKEOUT_FOLDER` | Older method: estimate from a Google Takeout export |
| `push` / `delete` | Older method: upload via the emulator / trash by clicking in the browser |

Everything the tool creates lives in `work/` (photos, logs, token, browser profile) and is
git-ignored.

## Credits

Built on the work of [xob0t](https://github.com/xob0t):
[gpmc](https://github.com/xob0t/google_photos_mobile_client) (mobile upload protocol) and
[Google Photos Toolkit](https://github.com/xob0t/Google-Photos-Toolkit) (web API), plus
[ReVanced](https://revanced.app).
