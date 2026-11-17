"""Cheap checks on a file inside a zip: image size, and whether a video was re-encoded by Google.

Only the first (and for some videos the last) part of each file is read.
"""
from __future__ import annotations

import struct
import zipfile

HEAD = 512 * 1024
GOOGLE_MARK = b"produced by Google"


def image_size(head: bytes) -> tuple[int, int] | None:
    if head[:8] == b"\x89PNG\r\n\x1a\n" and len(head) >= 24:
        w, h = struct.unpack(">II", head[16:24])
        return w, h
    if head[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(head):
            if head[i] != 0xFF:
                i += 1
                continue
            marker = head[i + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            seglen = struct.unpack(">H", head[i + 2:i + 4])[0]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                h, w = struct.unpack(">HH", head[i + 5:i + 9])
                return w, h
            i += 2 + seglen
    return None


def probe(zip_path: str, member: str, is_video: bool) -> dict:
    """Return {'width','height','google_encoded'} (values may be None if unknown)."""
    out = {"width": None, "height": None, "google_encoded": None}
    with zipfile.ZipFile(zip_path) as zf, zf.open(member) as f:
        head = f.read(HEAD)
        if not is_video:
            size = image_size(head)
            if size:
                out["width"], out["height"] = size
            return out
        if GOOGLE_MARK in head:
            out["google_encoded"] = True
            return out
        # moov atom may sit at the end of the file: read the tail too.
        info = zf.getinfo(member)
        if info.file_size > HEAD:
            tail_start = max(HEAD, info.file_size - HEAD)
            f.seek(tail_start)
            tail = f.read()
            out["google_encoded"] = GOOGLE_MARK in tail
        else:
            out["google_encoded"] = False
    return out
