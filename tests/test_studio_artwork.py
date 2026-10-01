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
        self.assertEqual(artwork.auto_backdrop(cands, {**r, "textless": False}), "/good.jpg")   # 4K isn't 1920x1080
        self.assertEqual(artwork.auto_backdrop({"backdrops": [C("/big.jpg", 1921, 1080)]}, r), None)   # 1 px bigger: off-size
        self.assertEqual(artwork.auto_backdrop(cands, {**r, "min_w": 3840, "min_h": 2160, "textless": False}), "/titled.jpg")
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

    def test_frames_are_art_like_any_other(self):
        frame = "jf-chapter:" + "a" * 32 + ":3"
        self.assertTrue(artwork.is_frame(frame))
        self.assertFalse(artwork.is_frame("jf-chapter:../x:1"))
        stored = []
        orig_fetch = artwork.fetch

        async def fetch(path):
            return b"frame-bytes"
        import sys
        import types
        fake = types.SimpleNamespace(
            store_custom_image=lambda data, kind: stored.append((data, kind)) or "custom:landscape/f.jpg",
            custom_art_bytes=lambda p: b"x" if stored else None)
        real = sys.modules.get("art_overrides")
        sys.modules["art_overrides"] = fake
        artwork.fetch = fetch
        try:
            p = asyncio.run(artwork.realize({"art_poster": frame, "art_crop": "0.5,0.5,1"}))
            self.assertEqual(p["art_poster"], "custom:landscape/f.jpg")
            asyncio.run(artwork.realize({"art_poster": frame}))
            self.assertEqual(len(stored), 1)                              # copied once, then reused
            self.assertEqual(asyncio.run(artwork.realize({"art_poster": "/t.jpg"}))["art_poster"], "/t.jpg")
            from studio import uploads
            self.assertIn("custom:landscape/f.jpg", uploads.studio_custom_paths())   # safe from clean-up
            self.assertEqual(db.query("SELECT * FROM uploads"), [])                  # not in your images
        finally:
            artwork.fetch = orig_fetch
            if real is None:
                sys.modules.pop("art_overrides", None)
            else:
                sys.modules["art_overrides"] = real

    def test_posters_never_carry_thumb_settings(self):
        row = {"tmdb_id": "1", "jf_type": "Movie"}
        url = engine.poster_url(row, "landscape_logo_pos=right&bottom_gradient=low", resolution=500, with_quality=False,
                                extra={"landscape_art": "original"})
        self.assertNotIn("landscape_", url)
        self.assertIn("bottom_gradient=low", url)
        url = engine.poster_url(row, "landscape_logo_pos=right", resolution=500, with_quality=False,
                                extra={"shape": "landscape"})
        self.assertIn("landscape_logo_pos=right", url)

    def test_pinned_thumb_is_still_generated(self):
        from urllib.parse import parse_qs, urlsplit
        row = {"jf_id": "m1", "tmdb_id": "949", "jf_type": "Movie", "name": "Heat"}
        key = rules.title_key(row)
        urls = []
        orig_render, orig_realize = engine.render, artwork.realize_path

        async def render(http, url):
            urls.append({k: v[0] for k, v in parse_qs(urlsplit(url).query).items()})
            return b"thumb", "image/jpeg"

        async def realize(path, crop="", aspect=16 / 9):
            return f"custom:landscape/{crop or 'x'}.jpg"
        engine.render, artwork.realize_path = render, realize
        import sys
        import types
        real_cfg = sys.modules.get("config")
        try:
            import config  # noqa: F401  (Linux: the real one)
        except Exception:
            sys.modules["config"] = types.SimpleNamespace(ACCESS_KEY="")
        try:
            artwork.set_choice(key, "thumb", "pinned", "/bd.jpg", "0.3,0.5,1.2")
            self.assertEqual(asyncio.run(artwork.resolve(row, "thumb")), (b"thumb", "image/jpeg"))
            q = urls[-1]
            self.assertEqual(q["shape"], "landscape")
            self.assertEqual(q["art_poster"], "custom:landscape/0.3,0.5,1.2.jpg")   # framed copy, styled
            artwork.set_logo(key, "thumb", "text")
            self.assertEqual(artwork.choice(key, "thumb")["path"], "/bd.jpg")          # the pin stays
            asyncio.run(artwork.resolve(row, "thumb"))
            self.assertEqual(urls[-1]["art_logo"], "text")
            artwork.set_logo(key, "thumb", "none")
            asyncio.run(artwork.resolve(row, "thumb"))
            self.assertEqual(urls[-1]["art_original"], "true")                          # its own title, no logo
            artwork.set_choice(key, "thumb", "auto")
            self.assertEqual(artwork.choice(key, "thumb")["logo"], "none")              # auto keeps the logo choice
            asyncio.run(artwork.resolve(row, "thumb"))
            self.assertNotIn("art_poster", urls[-1])
            self.assertEqual(urls[-1]["landscape_art"], "original")
            # A show only IMDb knows gets its thumb too, drawn on the backdrop you pinned.
            show = {"jf_id": "s9", "imdb_id": "tt6280300", "jf_type": "Series", "name": "Color Classics"}
            k9 = rules.title_key(show)
            artwork.set_logo(k9, "thumb", "custom:aaaaaaaaaaaaaaaa.png")
            self.assertEqual(asyncio.run(artwork.resolve(show, "thumb")), (b"thumb", "image/jpeg"))
            self.assertEqual((urls[-1]["imdb_id"], urls[-1]["shape"], urls[-1]["art_logo"]),
                             ("tt6280300", "landscape", "custom:aaaaaaaaaaaaaaaa.png"))
            self.assertNotIn("tmdb_id", urls[-1])
            self.assertNotIn("art_poster", urls[-1])
            artwork.set_choice(k9, "backdrop", "pinned", "custom:bbbbbbbbbbbbbbbb.jpg")
            asyncio.run(artwork.resolve(show, "thumb"))
            self.assertEqual(urls[-1]["art_poster"], "custom:landscape/x.jpg")
        finally:
            engine.render, artwork.realize_path = orig_render, orig_realize
            if real_cfg is None and isinstance(sys.modules.get("config"), types.SimpleNamespace):
                sys.modules.pop("config")

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
        m1 = self.jf.find("m1")
        m1["BackdropImageTags"] = ["jellyfins-own", "jellyfins-second"]
        self.sync()
        paths = sorted(p for _, p, _ in self.jf.art_uploads)
        self.assertEqual(paths, ["/Items/m1/Images/Backdrop", "/Items/m1/Images/Logo"])
        # Jellyfin adds a backdrop at the end; like a poster, ours replaces what was there.
        ours = m1["BackdropImageTags"]
        self.assertEqual(len(ours), 1)
        self.assertTrue(ours[0].startswith("ours-"))
        # Nothing changed: nothing sent again.
        self.sync()
        self.assertEqual(len(self.jf.art_uploads), 2)
        # A new backdrop replaces the one we sent.
        first = ours[0]
        self.bytes["backdrop"] = b"bd-2-longer"
        self.sync()
        self.assertEqual(len(m1["BackdropImageTags"]), 1)
        self.assertNotEqual(m1["BackdropImageTags"][0], first)
        # A changed logo is sent; a backdrop Jellyfin replaced is put back.
        self.bytes["logo"] = b"logo-2"
        m1["BackdropImageTags"] = ["someone-else"]
        counts = self.sync()
        self.assertEqual(len(self.jf.art_uploads), 5)
        self.assertEqual(counts.get("reverted"), 1)
        self.assertEqual(len(m1["BackdropImageTags"]), 1)
        self.assertTrue(m1["BackdropImageTags"][0].startswith("ours-"))

    def test_a_push_sees_what_jellyfin_holds_now(self):
        """Pushing one title skips the scan: it still notices an image Jellyfin replaced."""
        artwork.set_library_rules({"backdrop": {"enabled": True}})
        self.sync()
        m1 = self.jf.find("m1")
        m1["BackdropImageTags"] = ["someone-else"]
        m1["ImageTags"]["Primary"] = "someone-else"
        counts = self.sync(item_ids=["m1"])
        self.assertEqual(counts, {"reverted": 2})                 # poster and backdrop alike
        self.assertTrue(m1["BackdropImageTags"][0].startswith("ours-"))
        self.assertEqual(self.sync(item_ids=["m1"]), {"unchanged": 1})

    def test_keep_is_respected(self):
        artwork.set_library_rules({"logo": {"enabled": True}})
        artwork.set_choice(rules.title_key({"jf_id": "m1", "tmdb_id": "949", "jf_type": "Movie"}), "logo", "keep")
        self.sync()
        self.assertEqual(self.jf.art_uploads, [])


if __name__ == "__main__":
    unittest.main()
