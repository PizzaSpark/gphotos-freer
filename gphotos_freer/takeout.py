"""Read Google Takeout zips (and photos.google.com Download zips) without extracting them.

Every media file is paired with its JSON sidecar. Takeout's sidecar naming has many
variants; see `SidecarIndex.find` for the matching order.
"""
from __future__ import annotations

import glob
import json
import os
import re
import zipfile
from dataclasses import dataclass, field

MEDIA_EXTS = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif", ".bmp", ".tif", ".tiff",
    ".dng", ".cr2", ".nef", ".arw", ".raw", ".avif",
    ".mp4", ".mov", ".m4v", ".3gp", ".3g2", ".mkv", ".avi", ".webm", ".mts", ".m2ts", ".wmv", ".mpg",
}
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".3gp", ".3g2", ".mkv", ".avi", ".webm", ".mts", ".m2ts", ".wmv", ".mpg"}

# "-edited" in other UI languages, as Takeout writes them.
EDITED_SUFFIXES = ("-edited", "-bearbeitet", "-modifié", "-modificato", "-editado", "-bewerkt",
                   "-编辑", "-編集済み", "-измененный", "-edytowane", "-redigeret", "-redigert")

_SUPP = "supplemental-metadata"
# <base>[.<any prefix of supplemental-metadata>][(n)].json
_SIDECAR_RE = re.compile(r"^(?P<base>.+?)(?:\.(?P<supp>s[a-z-]*))?(?P<num>\(\d+\))?\.json$", re.I)
_NUM_RE = re.compile(r"^(?P<stem>.*)\((?P<n>\d+)\)$")


@dataclass
class Sidecar:
    path: str          # path inside the zip
    data: dict


@dataclass
class Item:
    zip_path: str
    member: str        # path inside the zip
    size: int
    sidecar: Sidecar | None = None
    match_rule: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def name(self) -> str:
        return os.path.basename(self.member)

    @property
    def folder(self) -> str:
        return os.path.dirname(self.member)

    @property
    def ext(self) -> str:
        return os.path.splitext(self.member)[1].lower()

    @property
    def is_video(self) -> bool:
        return self.ext in VIDEO_EXTS


def _parse_sidecar_name(fname: str):
    """Return (base, n) for a per-item sidecar file name, or None if it isn't one."""
    m = _SIDECAR_RE.match(fname)
    if not m:
        return None
    base, supp, num = m.group("base"), m.group("supp"), m.group("num")
    if supp and not _SUPP.startswith(supp.lower()):
        # Not a supplemental-metadata suffix after all (e.g. "photo.s.json" is fine, "x.something.json" isn't).
        base = base + "." + supp
    n = int(num[1:-1]) if num else 0
    return base, n


class SidecarIndex:
    def __init__(self):
        self.by_key: dict[tuple[str, str, int], Sidecar] = {}
        self.by_title: dict[tuple[str, str], list[Sidecar]] = {}
        self.by_dir: dict[str, list[tuple[str, int, Sidecar]]] = {}
        self.used: set[int] = set()

    def add(self, member: str, data: dict):
        folder, fname = os.path.split(member)
        parsed = _parse_sidecar_name(fname)
        if parsed is None:
            return
        base, n = parsed
        sc = Sidecar(member, data)
        self.by_key[(folder, base.lower(), n)] = sc
        self.by_dir.setdefault(folder, []).append((base.lower(), n, sc))
        title = data.get("title")
        if title:
            self.by_title.setdefault((folder, title.lower()), []).append(sc)

    def find(self, item: Item) -> tuple[Sidecar | None, str]:
        folder, name = item.folder, item.name
        stem, ext = os.path.splitext(name)
        low = name.lower()
        m = _NUM_RE.match(stem)
        n = int(m.group("n")) if m else 0
        plain = (m.group("stem") + ext) if m else name

        # 1. IMG.jpg -> IMG.jpg[.supplemental-metadata].json
        sc = self.by_key.get((folder, low, 0))
        if sc:
            return sc, "exact"
        # 2. IMG(1).jpg -> IMG.jpg[.supplemental-metadata](1).json
        if m:
            sc = self.by_key.get((folder, plain.lower(), n))
            if sc:
                return sc, "numbered"
        # 3. IMG-edited.jpg uses IMG.jpg's sidecar
        for suf in EDITED_SUFFIXES:
            if stem.lower().endswith(suf):
                orig = stem[: -len(suf)] + ext
                sc = self.by_key.get((folder, orig.lower(), 0))
                if sc:
                    return sc, "edited"
        # 4. sidecar "title" equals the file name (only when unambiguous)
        cands = self.by_title.get((folder, plain.lower()), [])
        if len(cands) == 1:
            return cands[0], "title"
        # 5. sidecar name was truncated (~51 chars): its base is a prefix of the file name
        best = None
        for base, bn, sc in self.by_dir.get(folder, []):
            if bn == n and len(base) >= 20 and plain.lower().startswith(base) and (best is None or len(base) > len(best[0])):
                best = (base, sc)
        if best:
            return best[1], "truncated"
        # 6. live/motion photo video: IMG.MP4 shares IMG.HEIC's sidecar
        for base, bn, sc in self.by_dir.get(folder, []):
            if bn == n and os.path.splitext(base)[0] == stem.lower() and base != low:
                return sc, "live-photo"
        return None, ""


def find_zips(path: str) -> list[str]:
    if os.path.isfile(path):
        return [path]
    return sorted(glob.glob(os.path.join(path, "**", "*.zip"), recursive=True))


def read_takeout(paths: list[str], progress=None) -> tuple[list[Item], list[Sidecar], list[str]]:
    """Return (items, unused_sidecars, problems). Items include `sidecar` when one was found.

    Items in trash folders or marked trashed are dropped.
    """
    items: list[Item] = []
    index = SidecarIndex()
    problems: list[str] = []
    for zp in paths:
        try:
            zf = zipfile.ZipFile(zp)
        except zipfile.BadZipFile as e:
            problems.append(f"{zp}: not a readable zip ({e})")
            continue
        with zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                member = info.filename
                low = member.lower()
                if "/trash/" in low or "/bin/" in low:
                    continue
                ext = os.path.splitext(low)[1]
                if ext == ".json":
                    try:
                        data = json.loads(zf.read(info))
                    except Exception as e:  # corrupt json is reported, not fatal
                        problems.append(f"{zp}:{member}: bad JSON ({e})")
                        continue
                    if isinstance(data, dict) and ("photoTakenTime" in data or "creationTime" in data):
                        index.add(member, data)
                elif ext in MEDIA_EXTS:
                    items.append(Item(zp, member, info.file_size))
        if progress:
            progress(zp)

    kept = []
    for it in items:
        sc, rule = index.find(it)
        if sc:
            index.used.add(id(sc))
            if sc.data.get("trashed"):
                continue
        it.sidecar, it.match_rule = sc, rule
        kept.append(it)
    unused = [sc for scs in index.by_dir.values() for _, _, sc in scs
              if id(sc) not in index.used and not sc.data.get("trashed")]
    return kept, unused, problems
