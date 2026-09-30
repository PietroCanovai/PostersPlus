"""Jellyfin's other images (Backdrop, Logo, Thumb): picking rules and the engine."""
import asyncio
import io
import json
import os
import tempfile
import unittest

import httpx

from studio import artwork, db, engine, prefs, rules
from test_studio_engine import FakeJellyfin, FakeRenderer

C = lambda path, w, h, lang=None: {"path": path, "width": w, "height": h, "language": lang}


class PickingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.connect(os.path.join(self.tmp.name, "studio.db"))

    def tearDown(self):
        db._conn.close()
        db._conn = None
        self.tmp.cleanup()

    def test_backdrop_rules(self):
        cands = {"backdrops": [C("/small.jpg", 1280, 720), C("/titled.jpg", 3840, 2160, "en"),
                               C("/square.jpg", 2000, 2000), C("/good.jpg", 1920, 1080)]}
        r = artwork.library_rules()["backdrop"]
        self.assertEqual(artwork.auto_backdrop(cands, r), "/good.jpg")                 # textless first
        self.assertEqual(artwork.auto_backdrop(cands, {**r, "textless": False}), "/titled.jpg")
        self.assertEqual(artwork.auto_backdrop(cands, {**r, "wide_only": False, "textless": False}), "/titled.jpg")
        self.assertIsNone(artwork.auto_backdrop({"backdrops": [C("/small.jpg", 1280, 720)]}, r))   # Jellyfin's stays

    def test_titled_backdrop_by_language(self):
        cands = {"backdrops": [C("/n.jpg", 1920, 1080), C("/fr.jpg", 1920, 1080, "fr"), C("/en.jpg", 1920, 1080, "en")]}
        self.assertEqual(artwork.titled_backdrop(cands, ["en"]), "/en.jpg")
        self.assertEqual(artwork.titled_backdrop(cands, ["it"]), "/fr.jpg")          # any titled one next

    def test_library_rules_are_validated(self):
        out = artwork.set_library_rules({"backdrop": {"enabled": True, "min_w": "2560", "bogus": 1},
                                         "thumb": {"source": "nonsense"}, "other": {}})
        self.assertTrue(out["backdrop"]["enabled"])
        self.assertEqual(out["backdrop"]["min_w"], 2560)
        self.assertNotIn("bogus", out["backdrop"])
        self.assertEqual(out["thumb"]["source"], "landscape")

    def test_choices(self):
        k = "tmdb:movie:1"
        self.assertEqual(artwork.choice(k, "logo")["mode"], "auto")
        self.assertFalse(artwork.managed(k, "logo"))                    # off in the library by default
        artwork.set_choice(k, "logo", "pinned", "/l.png")
        self.assertTrue(artwork.managed(k, "logo"))                     # a pin always counts
        artwork.set_choice(k, "backdrop", "keep")
        artwork.set_library_rules({"backdrop": {"enabled": True}})
        self.assertFalse(artwork.managed(k, "backdrop"))                # keep beats the library
        with self.assertRaises(ValueError):
            artwork.set_choice(k, "logo", "pinned", "")

    def test_frame(self):
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (1000, 1000), (10, 20, 30)).save(buf, format="JPEG")
        data, ctype = artwork._frame(buf.getvalue(), "0.5000,0.5000,1.000", 16 / 9)
        self.assertEqual((Image.open(io.BytesIO(data)).size, ctype), ((1000, 562), "image/jpeg"))
        same, _ = artwork._frame(buf.getvalue(), "", 16 / 9)
        self.assertEqual(same, buf.getvalue())


class EngineArtTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.connect(os.path.join(self.tmp.name, "studio.db"))
        prefs.set("jellyfin_url", "http://jf.test")
        prefs.set("jellyfin_api_key", "k")
        prefs.set("uploads_enabled", True)
        self.jf, self.pp = FakeJellyfin(), FakeRenderer()
        self.bytes = {"backdrop": b"bd-1", "logo": b"logo-1", "thumb": b"th-1"}
        self._orig = artwork.resolve

        async def fake_resolve(row, kind, **kw):
            if row["jf_id"] != "m1":
                return None
            return self.bytes[kind], "image/jpeg"
        artwork.resolve = fake_resolve

    def tearDown(self):
        artwork.resolve = self._orig
        db._conn.close()
        db._conn = None
        self.tmp.cleanup()

    def sync(self, **kw):
        rid = asyncio.run(engine.run(trigger="manual", dry_run=False,
                                     jf_transport=httpx.MockTransport(self.jf.handler),
                                     render_transport=httpx.MockTransport(self.pp.handler), **kw))
        return json.loads(db.query_one("SELECT counts FROM runs WHERE id = ?", (rid,))["counts"])

    def test_off_by_default(self):
        self.sync()
        self.assertEqual(self.jf.art_uploads, [])

    def test_backdrop_and_logo_when_on(self):
        artwork.set_library_rules({"backdrop": {"enabled": True}, "logo": {"enabled": True}})
        self.sync()
        paths = sorted(p for _, p, _ in self.jf.art_uploads)
        self.assertEqual(paths, ["/Items/m1/Images/Backdrop/0", "/Items/m1/Images/Logo"])   # backdrop replaces #0
        # Nothing changed: nothing sent again.
        self.sync()
        self.assertEqual(len(self.jf.art_uploads), 2)
        # A changed logo is sent; a backdrop Jellyfin replaced is put back.
        self.bytes["logo"] = b"logo-2"
        self.jf.find("m1")["BackdropImageTags"] = ["someone-else"]
        counts = self.sync()
        self.assertEqual(len(self.jf.art_uploads), 4)
        self.assertEqual(counts.get("reverted"), 1)

    def test_keep_is_respected(self):
        artwork.set_library_rules({"logo": {"enabled": True}})
        artwork.set_choice(rules.title_key({"jf_id": "m1", "tmdb_id": "949", "jf_type": "Movie"}), "logo", "keep")
        self.sync()
        self.assertEqual(self.jf.art_uploads, [])


if __name__ == "__main__":
    unittest.main()
