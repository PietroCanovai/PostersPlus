"""Studio's rules backup/restore, bulk actions, and the Playbill design."""
import os
import tempfile
import time
import unittest
from datetime import date

from fastapi import FastAPI
from fastapi.testclient import TestClient

from studio import api_library, auth, backup, db, prefs, rules

K1, K2 = "tmdb:movie:1", "tmdb:tv:2"


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.connect(os.path.join(self.tmp.name, "studio.db"))

    def tearDown(self):
        db._conn.close()
        db._conn = None
        self.tmp.cleanup()

    def _rules(self):
        a = rules.add_look(K1, {"poster": "/a.jpg", "colors": {"tint": "112233"}})
        rules.set_mode(K1, "pinned", a["look_id"])
        for p in ("/r1.jpg", "/r2.jpg"):
            rules.add_look(K2, {"poster": p, "in_rotation": True})
        rules.set_mode(K2, "rotation")
        rules.resolve(K2, today=date(2026, 10, 1), advance=True)
        rules.set_never(K2, "logo", "/bad.png", True)
        rules.set_hands_off(K1, True)

    def test_round_trip(self):
        self._rules()
        prefs.set("jellyfin_api_key", "SECRET")
        data = backup.export()
        self.assertNotIn("SECRET", str(data))
        before = {k: rules.resolve(k, look_override={}).params for k in (K1, K2)}
        for table in ("looks", "never", "titles"):
            db.execute(f"DELETE FROM {table}")
        # Other looks exist first, so restored ids won't match the old ones.
        rules.add_look("tmdb:movie:99", {"poster": "/x.jpg"})
        counts = backup.restore(data)
        self.assertEqual(counts, {"titles": 2, "looks": 3, "never": 1})
        t1 = rules.get_title(K1)
        self.assertEqual(t1["mode"], "pinned")
        self.assertTrue(t1["hands_off"])
        self.assertEqual(rules.get_look(t1["pinned_look_id"])["poster"], "/a.jpg")
        t2 = rules.get_title(K2)
        self.assertEqual(sorted(rules.get_look(i)["poster"] for i in t2["deck"]), ["/r1.jpg", "/r2.jpg"])
        self.assertEqual({k: rules.resolve(k, look_override={}).params for k in (K1, K2)}, before)
        self.assertEqual(rules.never(K2)["logo"], {"/bad.png"})

    def test_backdrop_logo_and_thumb_choices_are_backed_up(self):
        from studio import artwork
        artwork.set_choice(K1, "logo", "pinned", "/logo.png")
        artwork.set_choice(K1, "thumb", "keep")
        artwork.rotate(K2, "backdrop", "/b1.jpg", "0.5000,0.5000,1.000")
        artwork.rotate(K2, "backdrop", "/b2.jpg")
        artwork.set_library_rules({"backdrop": {"resize": True}})
        data = backup.export()
        db.execute("DELETE FROM jf_art")
        db.execute("DELETE FROM settings WHERE key = 'jf_art'")
        backup.restore(data)
        self.assertEqual(artwork.choice(K1, "logo")["path"], "/logo.png")
        self.assertEqual(artwork.choice(K1, "thumb")["mode"], "keep")
        c = artwork.choice(K2, "backdrop")
        self.assertEqual((c["mode"], [p["path"] for p in c["pool"]]), ("rotation", ["/b1.jpg", "/b2.jpg"]))
        self.assertTrue(artwork.library_rules()["backdrop"]["resize"])
        # A backup from before these were included leaves today's choices alone.
        old = {k: v for k, v in data.items() if k != "jf_art"}
        artwork.set_choice(K1, "backdrop", "pinned", "/kept.jpg")
        backup.restore(old)
        self.assertEqual(artwork.choice(K1, "backdrop")["path"], "/kept.jpg")

    def test_rejects_other_files(self):
        with self.assertRaises(ValueError):
            backup.restore({"hello": 1})

    def test_daily_files_are_pruned(self):
        for d in range(20):
            backup.write_daily(date(2026, 10, 1 + d))
        files = sorted(os.listdir(backup.backup_dir()))
        self.assertEqual(len(files), backup.KEEP)
        self.assertEqual(files[-1], "studio-2026-10-20.json")


class BulkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.connect(os.path.join(self.tmp.name, "studio.db"))
        now = time.time()
        for jf, tmdb in (("a", "1"), ("b", "1"), ("c", "3")):   # a and b are two copies of one film
            db.execute("INSERT INTO items (jf_id, library_id, library_name, jf_type, name, tmdb_id, status, added_at, "
                       "seen_at) VALUES (?, 'l', 'Movies', 'Movie', ?, ?, 'ok', ?, ?)", (jf, jf, tmdb, now, now))
        app = FastAPI()
        app.include_router(api_library.router)
        app.dependency_overrides[auth.require] = lambda: None
        self.c = TestClient(app)

    def tearDown(self):
        db._conn.close()
        db._conn = None
        self.tmp.cleanup()

    def test_hands_off_and_reviewed(self):
        r = self.c.post("/studio/api/bulk", json={"jf_ids": ["a", "b", "c"], "action": "hands_off"}).json()
        self.assertEqual(r["titles"], 2)   # a and b share a title
        self.assertTrue(rules.get_title("tmdb:movie:1")["hands_off"])
        self.c.post("/studio/api/bulk", json={"jf_ids": ["c"], "action": "reviewed"})
        self.assertTrue(rules.get_title("tmdb:movie:3")["reviewed_at"])
        self.c.post("/studio/api/bulk", json={"jf_ids": ["a"], "action": "reset"})
        self.assertFalse(rules.get_title("tmdb:movie:1")["hands_off"])

    def test_push_needs_uploads(self):
        r = self.c.post("/studio/api/bulk", json={"jf_ids": ["a"], "action": "push"})
        self.assertEqual(r.status_code, 400)

    def test_bad_requests(self):
        self.assertEqual(self.c.post("/studio/api/bulk", json={"jf_ids": ["a"], "action": "explode"}).status_code, 400)
        self.assertEqual(self.c.post("/studio/api/bulk", json={"jf_ids": [], "action": "reset"}).status_code, 400)


class PlaybillTests(unittest.TestCase):
    def test_compose(self):
        from PIL import Image
        from studio import playbill
        art = Image.new("RGB", (1200, 800), (200, 30, 30))
        out = playbill.compose(art, size=(500, 750), venue="WALTER KERR THEATRE")
        self.assertEqual(out.size, (500, 750))
        self.assertEqual(out.getpixel((250, 10)), playbill.YELLOW)            # yellow header
        self.assertEqual(out.getpixel((250, 600)), (200, 30, 30))             # the art below it
        self.assertEqual(out.getpixel((0, 400)), playbill.INK)                 # black frame

    def test_stage_render_both_designs(self):
        """The real theatre renderer (compositor included), with art served locally."""
        try:
            import main  # noqa: F401  (needs the Linux image's libraries)
        except Exception:
            self.skipTest("renderer not importable here")
        import asyncio
        import io
        from PIL import Image
        from studio import stage
        buf = io.BytesIO()
        Image.new("RGB", (900, 1350), (120, 20, 20)).save(buf, format="JPEG")
        art = buf.getvalue()

        async def fake_posters(show_id, force=False):
            return ["https://stagemedia.me/storage/posters/x.jpg"]

        async def fake_bytes(url):
            return art
        saved = stage.posters, stage.image_bytes
        stage.posters, stage.image_bytes = fake_posters, fake_bytes
        try:
            row = {"jf_id": "t", "name": "Hadestown", "stage_show_id": "2045", "productions": "Broadway|West End",
                   "venue": "Walter Kerr Theatre"}
            for extra in ({}, {"studio_template": "playbill"}, {"studio_template": "playbill", "playbill_venue": "WALTER KERR"}):
                data, ctype = asyncio.run(stage.render(row, "top_gradient=off", extra, 500))
                self.assertEqual(Image.open(io.BytesIO(data)).size, (500, 750))
        finally:
            stage.posters, stage.image_bytes = saved

    def test_venue(self):
        from studio import playbill
        self.assertEqual(playbill.venue_from_name("Hadestown - Broadway, 09-02-2024 - x"), "BROADWAY")
        self.assertEqual(playbill.venue_from_name("Death Note - West End Concert (Palladium Theatre), 2023 - y"),
                         "PALLADIUM THEATRE")
        self.assertEqual(playbill.venue_from_name("Evita"), "")


if __name__ == "__main__":
    unittest.main()
