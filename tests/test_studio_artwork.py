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

    def test_resize_what_you_pick(self):
        """With "resize" on, a backdrop of yours of another size goes out at exactly the rule's size."""
        from PIL import Image

        def jpeg(w, h):
            buf = io.BytesIO()
            Image.new("RGB", (w, h), (10, 20, 30)).save(buf, format="JPEG")
            return buf.getvalue()
        size = lambda data: Image.open(io.BytesIO(data)).size
        tall, exact = jpeg(1000, 1000), jpeg(1920, 1080)
        self.assertEqual(artwork._own_backdrop(tall, "")[0], tall)                     # off: sent as it is
        artwork.set_library_rules({"backdrop": {"resize": True}})
        self.assertEqual(size(artwork._own_backdrop(tall, "")[0]), (1920, 1080))       # cropped around the middle, enlarged
        self.assertEqual(size(artwork._own_backdrop(jpeg(3840, 2160), "")[0]), (1920, 1080))
        self.assertEqual(size(artwork._own_backdrop(jpeg(1921, 1080), "")[0]), (1920, 1080))
        self.assertEqual(artwork._own_backdrop(exact, "")[0], exact)                   # already right: untouched
        self.assertEqual(size(artwork._own_backdrop(tall, "0.0000,0.0000,2.000")[0]), (1920, 1080))   # your frame, then resized
        a = artwork._own_backdrop(tall, "")[0]
        self.assertEqual(a, artwork._own_backdrop(tall, "")[0])                        # same bytes every time: no re-upload
        artwork.set_library_rules({"backdrop": {"min_w": 2560, "min_h": 1440}})
        self.assertEqual(size(artwork._own_backdrop(exact, "")[0]), (2560, 1440))

    def test_backdrop_rotation(self):
        import random
        from datetime import date
        k = "tmdb:movie:1"
        artwork.set_choice(k, "backdrop", "pinned", "/a.jpg", "0.5000,0.5000,1.000")
        with self.assertRaises(ValueError):
            artwork.rotate(k, "logo", "/x.png")                        # only backdrops rotate
        c = artwork.rotate(k, "backdrop", "/b.jpg")
        self.assertEqual(c["mode"], "rotation")                         # the first one switches it on...
        self.assertEqual([p["path"] for p in c["pool"]], ["/a.jpg", "/b.jpg"])   # ...and the pinned one comes along
        self.assertEqual(c["pool"][0]["crop"], "0.5000,0.5000,1.000")
        self.assertTrue(artwork.managed(k, "backdrop"))
        c = artwork.rotate(k, "backdrop", "/c.jpg", "0.1000,0.2000,1.500")
        rng = random.Random(7)
        d1 = date(2026, 10, 3)
        first, rest = artwork.rotation_pick(artwork.choice(k, "backdrop"), today=d1, rng=rng)
        self.assertEqual(len(rest), 2)
        # A preview or a push on a later day shows the same one: only the nightly run moves on.
        again, _ = artwork.rotation_pick(artwork.choice(k, "backdrop"), today=date(2026, 10, 9), rng=rng)
        self.assertEqual(again, first)
        seen = [artwork.rotation_pick(artwork.choice(k, "backdrop"), advance=True, today=d1, rng=rng)[0]["path"]]
        self.assertEqual(seen[0], first["path"])                        # the first night starts the clock
        same_night, _ = artwork.rotation_pick(artwork.choice(k, "backdrop"), advance=True, today=d1, rng=rng)
        self.assertEqual(same_night, first)                             # a second copy of the title, same night
        for day in (4, 5):
            seen.append(artwork.rotation_pick(artwork.choice(k, "backdrop"), advance=True, today=date(2026, 10, day), rng=rng)[0]["path"])
        self.assertEqual(sorted(seen), ["/a.jpg", "/b.jpg", "/c.jpg"])  # every image once before any repeats
        nxt = artwork.rotation_pick(artwork.choice(k, "backdrop"), advance=True, today=date(2026, 10, 6), rng=rng)[0]["path"]
        self.assertNotEqual(nxt, seen[-1])                              # a new deck never opens on the one just shown
        self.assertEqual(artwork.chosen(artwork.choice(k, "backdrop"))["path"], nxt)
        # Taking one out, then the rest: back to automatic.
        today_path = nxt
        c = artwork.rotate(k, "backdrop", "/a.jpg", on=False)
        self.assertEqual(len(c["pool"]), 2)
        artwork.rotate(k, "backdrop", "/b.jpg", on=False)
        c = artwork.rotate(k, "backdrop", "/c.jpg", on=False)
        self.assertEqual((c["mode"], c["pool"]), ("auto", []))
        self.assertFalse(artwork.managed(k, "backdrop"))
        self.assertIsNotNone(today_path)
        with self.assertRaises(ValueError):
            artwork.set_choice(k, "backdrop", "rotation")                # nothing to rotate
        # Switching to Pinned and back keeps the pool.
        artwork.rotate(k, "backdrop", "/a.jpg")
        artwork.set_choice(k, "backdrop", "pinned", "/z.jpg")
        self.assertEqual(artwork.chosen(artwork.choice(k, "backdrop"))["path"], "/z.jpg")
        self.assertEqual(artwork.set_choice(k, "backdrop", "rotation")["mode"], "rotation")
        self.assertEqual(artwork.chosen(artwork.choice(k, "backdrop"))["path"], "/a.jpg")

    def test_a_pinned_upload_is_never_deleted(self):
        from studio import uploads
        k = "tmdb:movie:1"
        artwork.set_choice(k, "backdrop", "pinned", "custom:aaaaaaaaaaaaaaaa.jpg")
        artwork.set_choice(k, "logo", "pinned", "custom:bbbbbbbbbbbbbbbb.png")
        artwork.rotate("tmdb:movie:2", "backdrop", "custom:cccccccccccccccc.jpg")
        for p in ("custom:aaaaaaaaaaaaaaaa.jpg", "custom:bbbbbbbbbbbbbbbb.png", "custom:cccccccccccccccc.jpg"):
            self.assertTrue(uploads.in_use(p))
            self.assertIn(p, uploads.studio_custom_paths())             # safe from the Artwork tab's clean-up too


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

    def test_ours_is_the_backdrop_that_stays(self):
        """Jellyfin re-tags the backdrops it already had when one is uploaded: going by tags,
        Studio deleted its own upload and kept Jellyfin's.  It goes by the bytes now."""
        artwork.set_library_rules({"backdrop": {"enabled": True}})
        m1 = self.jf.find("m1")
        m1["BackdropImageTags"] = ["jellyfins-own", "jellyfins-second"]
        self.sync()
        tags = m1["BackdropImageTags"]
        self.assertEqual([self.jf.blobs[t] for t in tags], [b"bd-1"])

    def test_a_wrong_backdrop_is_noticed_and_replaced(self):
        """Studio believed its backdrop was in Jellyfin (hash and tag said so) while Jellyfin
        showed another image: every run now compares what Jellyfin really holds."""
        artwork.set_library_rules({"backdrop": {"enabled": True}})
        self.sync()
        m1 = self.jf.find("m1")
        tag = m1["BackdropImageTags"][0]
        self.jf.blobs[tag] = b"not-what-we-sent"                # same tag, another image
        counts = self.sync()
        self.assertEqual(counts.get("reverted"), 1)
        self.assertEqual([self.jf.blobs[t] for t in m1["BackdropImageTags"]], [b"bd-1"])
        self.assertEqual(self.sync().get("reverted"), None)     # and it stays put

    def test_an_unreadable_drive_is_an_error_not_a_success(self):
        artwork.set_library_rules({"backdrop": {"enabled": True}, "logo": {"enabled": True}})
        self.jf.unreadable.add("m1")
        rid_counts = self.sync()
        self.assertEqual(rid_counts.get("error"), 2)
        details = [r["detail"] for r in db.query("SELECT detail FROM run_items WHERE action = 'error'")]
        self.assertTrue(all("restart Jellyfin" in d for d in details), details)
        self.assertEqual(artwork.state("m1", "backdrop").get("pushed_hash"), None)   # not recorded as sent

    def test_rotation_moves_on_only_at_night(self):
        real = self._orig
        seen = []

        async def resolve(row, kind, **kw):
            if row["jf_id"] != "m1" or kind != "backdrop":
                return None
            pin = artwork.chosen(artwork.choice(rules.title_key(row), kind), advance=kw.get("advance", False))
            seen.append((pin["path"], kw.get("advance", False)))
            return pin["path"].encode(), "image/jpeg"
        artwork.resolve = resolve
        try:
            key = rules.title_key({"jf_id": "m1", "tmdb_id": "949", "jf_type": "Movie"})
            artwork.rotate(key, "backdrop", "/a.jpg")
            artwork.rotate(key, "backdrop", "/b.jpg")
            self.sync()                                              # a manual run: today's, not advanced
            self.assertFalse(seen[-1][1])
            today = seen[-1][0]
            self.assertEqual(self.jf.blobs[self.jf.find("m1")["BackdropImageTags"][0]], today.encode())
            self.sync(item_ids=["m1"])
            self.assertEqual(seen[-1], (today, False))               # nor does pushing the title
            rid = asyncio.run(engine.run(trigger="schedule", dry_run=False,
                                         jf_transport=httpx.MockTransport(self.jf.handler),
                                         render_transport=httpx.MockTransport(self.pp.handler)))
            self.assertTrue(seen[-1][1])                             # the nightly run does
            self.assertIsNotNone(rid)
        finally:
            artwork.resolve = real

    def test_missing_lists_what_jellyfin_lacks(self):
        from studio import api_library
        self.sync()
        rows = {r["jf_id"]: r["missing"] for r in api_library.missing_rows()}
        self.assertEqual(rows["m1"], ["backdrop", "logo", "thumb"])     # the fake library has posters only
        m1 = self.jf.find("m1")
        m1["BackdropImageTags"] = ["b"]
        m1["ImageTags"]["Logo"] = "l"
        prefs.set("uploads_enabled", False)
        del m1["ImageTags"]["Primary"]
        self.sync()                                                     # a preview run reads the library too
        rows = {r["jf_id"]: r["missing"] for r in api_library.missing_rows()}
        self.assertEqual(rows["m1"], ["poster", "thumb"])
        self.assertIn("m2", rows)                                       # left-alone titles count too
        self.assertEqual(api_library.missing_count(), len(rows))        # the menu's count agrees

    def test_keep_is_respected(self):
        artwork.set_library_rules({"logo": {"enabled": True}})
        artwork.set_choice(rules.title_key({"jf_id": "m1", "tmdb_id": "949", "jf_type": "Movie"}), "logo", "keep")
        self.sync()
        self.assertEqual(self.jf.art_uploads, [])


if __name__ == "__main__":
    unittest.main()
