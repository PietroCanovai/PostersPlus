"""The Playbill cover: its header, the theatre it names, and the generator's API."""
import io
import os
import sys
import tempfile
import time
import types
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from studio import api_library, artwork, auth, db, engine, playbill, uploads


def _ink(img, box):
    """The bounding box of the black pixels inside *box* (left, top, right, bottom)."""
    part = img.crop(box).convert("L").point(lambda v: 255 if v < 60 else 0)
    l, t, r, b = part.getbbox()
    return box[0] + l, box[1] + t, box[0] + r, box[1] + b


class HeaderTests(unittest.TestCase):
    def test_the_covers_proportions(self):
        head = playbill.header(1000, "New Amsterdam Theatre")
        self.assertEqual(head.size, (1000, 317))
        self.assertEqual(head.getpixel((5, 5)), playbill.YELLOW)
        # The wordmark: 0.0478 to 0.9438 of the width, 0.0547 down, 0.1165 high (below the ® mark's row).
        l, t, r, b = _ink(head, (0, 0, 940, 200))
        self.assertAlmostEqual(l, 48, delta=3)
        self.assertAlmostEqual(t, 55, delta=3)
        self.assertAlmostEqual(b, 171, delta=3)
        self.assertAlmostEqual(_ink(head, (0, 80, 1000, 200))[2], 944, delta=3)
        # The theatre: capitals 0.0247 high, 0.228 down, centred.
        l, t, r, b = _ink(head, (0, 200, 1000, 300))
        self.assertAlmostEqual(t, 228, delta=3)
        self.assertAlmostEqual(b - t, 25, delta=4)
        self.assertAlmostEqual(l + r, 1000, delta=6)
        self.assertAlmostEqual(r - l, 456, delta=30)      # as wide as it is on Aladdin's cover

    def test_a_long_theatre_name_still_fits(self):
        head = playbill.header(1000, "Kit Kat Club at the August Wilson Theatre and a few more words besides")
        l, _, r, _ = _ink(head, (0, 200, 1000, 317))
        self.assertGreater(l, 60)
        self.assertLess(r, 940)
        self.assertEqual(playbill.header(1000, "").size, (1000, 317))      # no theatre: just the wordmark

    def test_compose(self):
        art = Image.new("RGB", (1200, 800), (200, 30, 30))
        out = playbill.compose(art, venue="Walter Kerr Theatre")
        self.assertEqual(out.size, (1000, 1500))
        self.assertEqual(out.getpixel((500, 20)), playbill.YELLOW)          # the header
        self.assertEqual(out.getpixel((500, 330)), (200, 30, 30))           # the art starts right under it
        self.assertEqual(out.getpixel((500, 1400)), (200, 30, 30))
        self.assertEqual(out.getpixel((0, 800)), playbill.INK)              # the black frame
        self.assertAlmostEqual(playbill.art_aspect(), 1000 / 1183, places=4)
        self.assertEqual(playbill.compose(art, size=(500, 750)).size, (500, 750))

    def test_the_frame_picks_the_part_of_the_image(self):
        art = Image.new("RGB", (2000, 1000), (0, 0, 200))
        art.paste((0, 200, 0), (0, 0, 1000, 1000))                          # left half green, right half blue
        left = playbill.compose(art, crop=(0.0, 0.5, 1.0)).getpixel((500, 900))
        right = playbill.compose(art, crop=(1.0, 0.5, 1.0)).getpixel((500, 900))
        self.assertEqual((left, right), ((0, 200, 0), (0, 0, 200)))


class VenueTests(unittest.TestCase):
    def rec(self, production, venue, date="2020-01-01"):
        return {"production": production, "venue": venue, "date": date}

    def test_broadway_first_then_west_end_then_anywhere(self):
        r = self.rec
        both = [r("West End", "Lyric Theatre", "2019-01-01"), r("Broadway", "Walter Kerr Theatre", "2024-02-09")]
        self.assertEqual(playbill.pick_venue(both), "Walter Kerr Theatre")
        self.assertEqual(playbill.venues(both), ["Walter Kerr Theatre", "Lyric Theatre"])
        self.assertEqual(playbill.pick_venue([r("First UK Tour", "Palace Theatre, Manchester"), r("West End", "Savoy Theatre")]),
                         "Savoy Theatre")
        self.assertEqual(playbill.pick_venue([r("Off-Broadway", "New World Stages"), r("Vienna", "Raimund Theater")]),
                         "New World Stages")                                 # nothing better: the first one
        self.assertEqual(playbill.pick_venue([r("Off-Broadway", "New World Stages"), r("West End", "Savoy Theatre")]),
                         "Savoy Theatre")

    def test_the_original_run_before_revivals(self):
        r = self.rec
        self.assertEqual(playbill.pick_venue([r("Broadway Revival", "Studio 54", "1998-03-19"),
                                              r("Broadway", "Broadhurst Theatre", "2001-01-01")]), "Broadhurst Theatre")
        self.assertEqual(playbill.pick_venue([r("Fifth West End Revival", "Kit Kat Club at the Playhouse", "2025-10-01"),
                                              r("Second West End Revival", "Lyric Theatre", "2006-10-01")]), "Lyric Theatre")
        self.assertEqual(playbill.pick_venue([r("Broadway", ""), r("", "")]), "")
        self.assertEqual(playbill.pick_venue([]), "")


class ApiTests(unittest.TestCase):
    """The generator, with the image store and Jellyfin faked."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.connect(os.path.join(self.tmp.name, "studio.db"))
        now = time.time()
        db.execute("INSERT INTO items (jf_id, library_id, library_name, jf_type, name, tmdb_id, status, added_at, seen_at) "
                   "VALUES ('film', 'l', 'Movies', 'Movie', 'Cabaret', '1', 'ok', ?, ?)", (now, now))
        db.execute("INSERT INTO items (jf_id, library_id, library_name, jf_type, name, stage_show_id, venue, status, added_at, "
                   "seen_at) VALUES ('show', 't', 'Theatre', 'Series', 'Hadestown', '2045', 'Walter Kerr Theatre', 'ok', ?, ?)",
                   (now, now))
        buf = io.BytesIO()
        Image.new("RGB", (1600, 900), (30, 90, 160)).save(buf, format="JPEG")
        self.source, self.stored = buf.getvalue(), {}

        def store(data, kind):
            self.stored[f"custom:pb{len(self.stored)}.jpg"] = (data, kind)
            return f"custom:pb{len(self.stored) - 1}.jpg"

        def provider_of(path):
            if not path.startswith(("custom:", "/")):
                raise ValueError("Not an image path")
            return "custom" if path.startswith("custom:") else "tmdb"
        self.real = sys.modules.get("art_overrides")
        sys.modules["art_overrides"] = types.SimpleNamespace(store_custom_image=store, provider_of=provider_of)
        self.fetch, self.client = artwork.fetch, engine.shared_client

        async def fetch(path):
            return self.source
        artwork.fetch = fetch
        app = FastAPI()
        app.include_router(api_library.router)
        app.dependency_overrides[auth.require] = lambda: None
        self.c = TestClient(app)

    def tearDown(self):
        artwork.fetch, engine.shared_client = self.fetch, self.client
        if self.real is None:
            sys.modules.pop("art_overrides", None)
        else:
            sys.modules["art_overrides"] = self.real
        db._conn.close()
        db._conn = None
        self.tmp.cleanup()

    def test_preview(self):
        r = self.c.get("/studio/api/title/film/playbill", params={"path": "/b.jpg", "crop": "0.2,0.5,1.5", "venue": "Studio 54"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-type"], "image/jpeg")
        self.assertEqual(Image.open(io.BytesIO(r.content)).size, (500, 750))
        self.assertEqual(self.stored, {})                                           # a preview keeps nothing
        self.assertEqual(self.c.get("/studio/api/title/film/playbill").status_code, 400)                      # no image
        self.assertEqual(self.c.get("/studio/api/title/film/playbill", params={"path": "ftp://x"}).status_code, 400)
        self.assertEqual(self.c.get("/studio/api/title/film/playbill", params={"path": "/b.jpg", "crop": "9,9,9"}).status_code, 400)

    def test_save_adds_a_poster_of_yours(self):
        r = self.c.post("/studio/api/title/film/playbill", json={"path": "custom:mine.jpg", "crop": "0.5,0.5,1", "venue": "Studio 54"})
        self.assertEqual(r.status_code, 200)
        path = r.json()["path"]
        data, kind = self.stored[path]
        self.assertEqual(kind, "poster")
        cover = Image.open(io.BytesIO(data))
        self.assertEqual(cover.size, (1000, 1500))
        self.assertEqual(cover.convert("RGB").getpixel((500, 20)), playbill.YELLOW)
        mine = uploads.for_title("tmdb:movie:1")
        self.assertEqual([(u["path"], u["kind"], u["name"], u["own_title"]) for u in mine],
                         [(path, "poster", "Playbill · Studio 54", True)])           # it has its own header: no logo on it

    def test_the_theatre_is_offered(self):
        class Jf:
            async def recordings(self, series_id):
                return [{"production": "West End", "venue": "Lyric Theatre", "date": "2025-01-01"},
                        {"production": "Broadway", "venue": "Walter Kerr Theatre", "date": "2021-09-01"}]
        engine.shared_client = lambda: Jf()
        d = self.c.get("/studio/api/title/show/playbill/venues").json()
        self.assertEqual((d["venue"], d["venues"]), ("Walter Kerr Theatre", ["Walter Kerr Theatre", "Lyric Theatre"]))
        self.assertEqual(d["size"], [1000, 1500])

        class Down:
            async def recordings(self, series_id):
                raise RuntimeError("Jellyfin is away")
        engine.shared_client = lambda: Down()
        self.assertEqual(self.c.get("/studio/api/title/show/playbill/venues").json()["venue"], "Walter Kerr Theatre")   # the scan's
        self.assertEqual(self.c.get("/studio/api/title/film/playbill/venues").json()["venues"], [])     # a film: you type it


if __name__ == "__main__":
    unittest.main()
