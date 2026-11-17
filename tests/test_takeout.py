import json
import os
import struct
import tempfile
import unittest
import zipfile

from gphotos_freer import audit, takeout

P = "Takeout/Google Photos/Photos from 2019/"


def sidecar(title, uploaded, taken=None, **extra):
    d = {"title": title, "creationTime": {"timestamp": str(uploaded)},
         "photoTakenTime": {"timestamp": str(taken or uploaded)}, "url": f"https://photos.google.com/photo/{title}"}
    d.update(extra)
    return json.dumps(d)


def jpeg(w, h, pad=0):
    sof = b"\xff\xc0" + struct.pack(">HBHH", 11, 8, h, w) + b"\x01\x11\x00"
    return b"\xff\xd8" + sof + b"\x00" * pad + b"\xff\xd9"


OLD, NEW = 1560000000, 1700000000  # 2019, 2023


class MatchTests(unittest.TestCase):
    def build(self, files):
        d = tempfile.mkdtemp()
        zp = os.path.join(d, "t.zip")
        with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
            for name, data in files.items():
                z.writestr(name, data)
        items, unused, problems = takeout.read_takeout([zp])
        return {i.name: i for i in items}, unused, problems

    def test_variants(self):
        long_name = "Screenshot_20191230_203331_com.facebook.orca_extra_long_name.jpg"
        files = {
            P + "a.jpg": jpeg(10, 10), P + "a.jpg.supplemental-metadata.json": sidecar("a.jpg", OLD),
            P + "b.jpg": jpeg(10, 10), P + "b.jpg.json": sidecar("b.jpg", OLD),
            P + "c(1).jpg": jpeg(10, 10), P + "c.jpg.supplemental-metadata(1).json": sidecar("c.jpg", NEW),
            P + "c.jpg": jpeg(10, 10), P + "c.jpg.supplemental-metadata.json": sidecar("c.jpg", OLD),
            P + "d-edited.jpg": jpeg(10, 10), P + "d.jpg": jpeg(10, 10), P + "d.jpg.supplemental-metadata.json": sidecar("d.jpg", OLD),
            P + long_name: jpeg(10, 10), P + long_name[:40] + ".suppl.json": sidecar(long_name, OLD),
            P + "e.HEIC": b"x", P + "e.MP4": b"x", P + "e.HEIC.supplemental-metadata.json": sidecar("e.HEIC", OLD),
            P + "gone.jpg.supplemental-metadata.json": sidecar("gone.jpg", OLD),
            P + "t.jpg": jpeg(10, 10), P + "t.jpg.json": sidecar("t.jpg", OLD, trashed=True),
            P + "metadata.json": json.dumps({"title": "Album", "date": {}}),
        }
        items, unused, problems = self.build(files)
        rules = {n: i.match_rule for n, i in items.items()}
        self.assertEqual(rules["a.jpg"], "exact")
        self.assertEqual(rules["b.jpg"], "exact")
        self.assertEqual(rules["c(1).jpg"], "numbered")
        self.assertEqual(items["c(1).jpg"].sidecar.data["creationTime"]["timestamp"], str(NEW))
        self.assertEqual(rules["c.jpg"], "exact")
        self.assertEqual(rules["d-edited.jpg"], "edited")
        self.assertIn(rules[long_name], ("truncated", "title"))
        self.assertEqual(rules["e.MP4"], "live-photo")
        self.assertNotIn("t.jpg", items)  # trashed
        self.assertEqual([os.path.basename(s.path) for s in unused], ["gone.jpg.supplemental-metadata.json"])
        self.assertEqual(problems, [])


class ClassifyTests(unittest.TestCase):
    def item(self, name, size, uploaded):
        it = takeout.Item("z", P + name, size)
        it.sidecar = takeout.Sidecar("s", json.loads(sidecar(name, uploaded)))
        return it

    def test_rules(self):
        self.assertEqual(audit.classify(self.item("a.jpg", 1, NEW), None)[0], "check")
        shared = self.item("s.jpg", 1, NEW)
        shared.sidecar.data["googlePhotosOrigin"] = {"fromSharedAlbum": {}}
        self.assertEqual(audit.classify(shared, None)[0], "counts")
        self.assertEqual(audit.classify(self.item("a.jpg", 400_000, OLD), {"width": 4608, "height": 3456})[0], "free")
        self.assertEqual(audit.classify(self.item("a.jpg", 3_000_000, OLD), {"width": 4608, "height": 3456})[0], "check")
        self.assertEqual(audit.classify(self.item("a.jpg", 400_000, OLD), {"width": 6000, "height": 4000})[0], "counts")
        self.assertEqual(audit.classify(self.item("v.mp4", 1, OLD), {"google_encoded": True})[0], "free")
        self.assertEqual(audit.classify(self.item("v.mp4", 1, OLD), {"google_encoded": False})[0], "check")
        no_meta = takeout.Item("z", P + "x.jpg", 1)
        self.assertEqual(audit.classify(no_meta, None)[0], "check")

    def test_jpeg_size(self):
        from gphotos_freer.probe import image_size
        self.assertEqual(image_size(jpeg(4608, 3456)), (4608, 3456))


if __name__ == "__main__":
    unittest.main()


class FilenameDateTests(unittest.TestCase):
    def test_patterns(self):
        from gphotos_freer.inbox import date_from_name as d
        self.assertEqual(str(d("20190915_075026.jpg")), "2019-09-15 07:50:26")
        self.assertEqual(str(d("IMG_20250107_154557_399.jpg")), "2025-01-07 15:45:57")
        self.assertEqual(str(d("Screenshot_20200512-130051_YouTube.jpg")), "2020-05-12 13:00:51")
        self.assertEqual(str(d("VID_20241228_190846.mp4")), "2024-12-28 19:08:46")
        self.assertEqual(d("FB_IMG_1735052659874.jpg").year, 2024)
        self.assertEqual(str(d("2020-05-12 party.jpg")), "2020-05-12 00:00:00")
        self.assertIsNone(d("received_870899590044620.png"))
        self.assertIsNone(d("92ed37005dec88917e7d2fb40347a1d1.mp4"))
