"""Studio's sync engine against a fake Jellyfin and a fake renderer."""
import asyncio
import base64
import json
import os
import tempfile
import unittest
from datetime import datetime
from urllib.parse import parse_qs, urlsplit

import httpx

from studio import auth, db, engine, prefs
from studio.jellyfin import quality_tokens


class FakeJellyfin:
    """Just enough of Jellyfin's API: libraries, items, image tags, uploads."""

    def __init__(self):
        self.libraries = [
            {"Name": "Movies", "ItemId": "lib-m", "CollectionType": "movies"},
            {"Name": "TV", "ItemId": "lib-t", "CollectionType": "tvshows"},
            {"Name": "Concerts", "ItemId": "lib-c", "CollectionType": "musicvideos"},
            {"Name": "Music", "ItemId": "lib-x", "CollectionType": "music"},
        ]
        self.items = {
            "lib-m": [self._item("m1", "Movie", "Heat", tmdb="949", imdb="tt0113277"),
                      self._item("m2", "Movie", "Unknown Home Video")],
            "lib-t": [self._item("s1", "Series", "Dark", tmdb="70523")],
            "lib-c": [self._item("c1", "MusicVideo", "Live at Wembley (YouTube)"),
                      self._item("c2", "MusicVideo", "Stop Making Sense", tmdb="24128")],
            "lib-x": [],
        }
        self.uploads: list[tuple[str, bytes, str]] = []
        self.art_uploads: list[tuple[str, str, bytes]] = []
        self._tag = 0

    def _item(self, id_, type_, name, tmdb=None, imdb=None):
        ids = {}
        if tmdb:
            ids["Tmdb"] = tmdb
        if imdb:
            ids["Imdb"] = imdb
        return {"Id": id_, "Type": type_, "Name": name, "ProviderIds": ids, "ImageTags": {"Primary": f"orig-{id_}"},
                "MediaSources": [{"Path": f"/media/{name}.mkv",
                                  "MediaStreams": [{"Type": "Video", "Width": 1920, "Height": 1080}]}]}

    def find(self, id_):
        for rows in self.items.values():
            for it in rows:
                if it["Id"] == id_:
                    return it
        return None

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, q = request.url.path, dict(request.url.params)
        if path == "/Library/VirtualFolders":
            return httpx.Response(200, json=self.libraries)
        if path == "/Items" and "Ids" in q:
            it = self.find(q["Ids"])
            return httpx.Response(200, json={"Items": [it] if it else [], "TotalRecordCount": 1 if it else 0})
        if path == "/Items":
            wanted = set(q.get("IncludeItemTypes", "").split(","))
            rows = [r for r in self.items.get(q.get("ParentId"), []) if r["Type"] in wanted]
            start = int(q.get("StartIndex", 0))
            return httpx.Response(200, json={"Items": rows[start:], "TotalRecordCount": len(rows)})
        if path.startswith("/Shows/") and path.endswith("/Seasons"):
            return httpx.Response(200, json={"Items": [
                {"Id": "s1-0", "Type": "Season", "IndexNumber": 0, "Name": "Specials", "ImageTags": {}},
                {"Id": "s1-1", "Type": "Season", "IndexNumber": 1, "Name": "Season 1", "ImageTags": {"Primary": "x"}},
            ]})
        if "/Images/" in path and request.method == "POST":
            parts = path.split("/")          # /Items/<id>/Images/<Type>[/<index>]
            id_, kind = parts[2], parts[4]
            self._tag += 1
            it = self.find(id_)
            if kind == "Primary":
                self.uploads.append((id_, base64.b64decode(request.content), request.headers["content-type"]))
                it["ImageTags"]["Primary"] = f"ours-{self._tag}"
            else:
                self.art_uploads.append((id_, path, base64.b64decode(request.content)))
                if kind == "Backdrop":
                    it["BackdropImageTags"] = [f"ours-{self._tag}"]
                else:
                    it["ImageTags"][kind] = f"ours-{self._tag}"
            return httpx.Response(204)
        return httpx.Response(404)


class FakeRenderer:
    def __init__(self):
        self.version = {"949": b"heat-v1", "70523": b"dark-v1", "24128": b"sms-v1"}
        self.requests: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        q = {k: v[0] for k, v in parse_qs(urlsplit(str(request.url)).query).items()}
        self.requests.append(q)
        return httpx.Response(200, content=self.version[q["tmdb_id"]], headers={"content-type": "image/jpeg"})


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.connect(os.path.join(self.tmp.name, "studio.db"))
        prefs.set("jellyfin_url", "http://jf.test")
        prefs.set("jellyfin_api_key", "k")
        self.jf, self.pp = FakeJellyfin(), FakeRenderer()

    def tearDown(self):
        db._conn.close()
        db._conn = None
        self.tmp.cleanup()

    def run_sync(self, dry_run=False, **kw):
        engine.progress.cancel = False
        rid = asyncio.run(engine.run(trigger="manual", dry_run=dry_run,
                                     jf_transport=httpx.MockTransport(self.jf.handler),
                                     render_transport=httpx.MockTransport(self.pp.handler), **kw))
        return db.query_one("SELECT * FROM runs WHERE id = ?", (rid,))

    def counts(self, run):
        return json.loads(run["counts"])

    def test_uploads_off_means_preview(self):
        run = self.run_sync()
        self.assertTrue(run["dry_run"])
        self.assertEqual(self.counts(run).get("would_upload"), 3)
        self.assertEqual(self.jf.uploads, [])

    def test_first_upload_then_unchanged(self):
        prefs.set("uploads_enabled", True)
        run = self.run_sync()
        self.assertEqual(run["status"], "done")
        self.assertEqual(self.counts(run), {"uploaded": 3, "skipped": 2})
        self.assertEqual({u[0] for u in self.jf.uploads}, {"m1", "s1", "c2"})
        self.assertTrue(all(u[2] == "image/jpeg" for u in self.jf.uploads))
        self.assertEqual(sorted(u[1] for u in self.jf.uploads), [b"dark-v1", b"heat-v1", b"sms-v1"])
        # Nothing changed: the second run uploads nothing.
        run = self.run_sync()
        self.assertEqual(self.counts(run), {"unchanged": 3, "skipped": 2})
        self.assertEqual(len(self.jf.uploads), 3)

    def test_changed_poster_is_uploaded(self):
        prefs.set("uploads_enabled", True)
        self.run_sync()
        self.pp.version["949"] = b"heat-v2"
        run = self.run_sync()
        self.assertEqual(self.counts(run).get("uploaded"), 1)
        self.assertEqual(self.jf.uploads[-1][:2], ("m1", b"heat-v2"))

    def test_revert_is_put_back(self):
        prefs.set("uploads_enabled", True)
        self.run_sync()
        self.jf.find("s1")["ImageTags"]["Primary"] = "someone-else"
        run = self.run_sync()
        self.assertEqual(self.counts(run).get("reverted"), 1)
        self.assertEqual(self.jf.uploads[-1][0], "s1")
        row = db.query_one("SELECT revert_count, pushed_tag FROM items WHERE jf_id = 's1'")
        self.assertEqual(row["revert_count"], 1)
        self.assertTrue(row["pushed_tag"].startswith("ours-"))

    def test_unmatched_policies(self):
        self.run_sync()
        st = {r["jf_id"]: r["status"] for r in db.query("SELECT jf_id, status FROM items")}
        self.assertEqual(st["m2"], engine.NEEDS_MATCH)     # Movies: flagged
        self.assertEqual(st["c1"], engine.LEFT_ALONE)      # Concerts: left alone
        self.assertNotIn("x", st)                          # Music isn't managed

    def test_render_url(self):
        self.run_sync()
        heat = next(q for q in self.pp.requests if q["tmdb_id"] == "949")
        self.assertEqual(heat["type"], "movie")
        self.assertEqual(heat["imdb_id"], "tt0113277")
        self.assertEqual(heat["resolution"], "1000")
        self.assertEqual(heat["sash_mode"], "notch")
        self.assertEqual(heat["primary_client"], "jellyfin")
        dark = next(q for q in self.pp.requests if q["tmdb_id"] == "70523")
        self.assertEqual(dark["type"], "tv")
        concert = next(q for q in self.pp.requests if q["tmdb_id"] == "24128")
        self.assertEqual(concert["type"], "movie")   # concerts (MusicVideo) render as films

    def test_force_reuploads(self):
        prefs.set("uploads_enabled", True)
        self.run_sync()
        run = self.run_sync(force=True, item_ids=["m1"])
        self.assertEqual(self.counts(run), {"uploaded": 1})

    def test_schedule(self):
        prefs.set("schedule_time", "04:00")
        self.assertFalse(engine.due(datetime(2026, 10, 1, 5, 0)))      # off
        prefs.set("schedule_enabled", True)
        self.assertFalse(engine.due(datetime(2026, 10, 1, 3, 59)))
        self.assertTrue(engine.due(datetime(2026, 10, 1, 4, 0)))
        self.assertTrue(engine.due(datetime(2026, 10, 1, 13, 0)))       # missed: catch up
        db.set_setting("last_scheduled_date", "2026-10-01")
        self.assertFalse(engine.due(datetime(2026, 10, 1, 13, 0)))
        self.assertEqual(engine.next_run_at(datetime(2026, 10, 1, 13, 0)), datetime(2026, 10, 2, 4, 0))
        self.assertEqual(engine.next_run_at(datetime(2026, 10, 2, 1, 0)), datetime(2026, 10, 2, 4, 0))

    def test_enabling_schedule_late_waits_for_tomorrow(self):
        engine.mark_schedule_enabled(datetime(2026, 10, 1, 14, 0))
        prefs.set("schedule_enabled", True)
        self.assertFalse(engine.due(datetime(2026, 10, 1, 14, 1)))
        self.assertTrue(engine.due(datetime(2026, 10, 2, 4, 0)))


class TheatreTests(EngineTests.__bases__[0]):
    """StageMedia recordings: no TMDB id, a StageMediaShowId, drawn by studio.stage."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.connect(os.path.join(self.tmp.name, "studio.db"))
        prefs.set("jellyfin_url", "http://jf.test")
        prefs.set("jellyfin_api_key", "k")
        prefs.set("uploads_enabled", True)
        prefs.set("libraries", {"lib-th": {"enabled": True, "unmatched": "leave"}})
        self.jf = FakeJellyfin()
        self.jf.libraries = [{"Name": "Theatre", "ItemId": "lib-th", "CollectionType": "movies"}]
        rec = lambda i, name: {**self.jf._item(i, "Movie", name), "ProviderIds": {"StageMediaShowId": "42"}}
        self.jf.items = {"lib-th": [rec("t1", "Hadestown - Broadway, 09-02-2024 - a"),
                                    rec("t2", "Hadestown - West End, xx-03-2025 - b")]}
        from studio import stage
        self.stage = stage
        self.calls = []

        async def fake_render(row, style, params, resolution):
            self.calls.append((row["jf_id"], params, resolution))
            return b"stage-" + row["name"][:9].encode(), "image/jpeg"
        self._orig = stage.render
        stage.render = fake_render

    def tearDown(self):
        self.stage.render = self._orig
        db._conn.close()
        db._conn = None
        self.tmp.cleanup()

    def sync(self):
        rid = asyncio.run(engine.run(trigger="manual", dry_run=False,
                                     jf_transport=httpx.MockTransport(self.jf.handler),
                                     render_transport=httpx.MockTransport(lambda r: httpx.Response(500))))
        return json.loads(db.query_one("SELECT counts FROM runs WHERE id = ?", (rid,))["counts"])

    def test_without_a_key_theatre_is_left_alone(self):
        self.assertEqual(self.sync(), {"skipped": 2})
        self.assertEqual({r["status"] for r in db.query("SELECT status FROM items")}, {engine.LEFT_ALONE})

    def test_with_a_key_theatre_is_drawn_by_the_stage_renderer(self):
        prefs.set("stagemedia_key", "sm")
        self.assertEqual(self.sync(), {"uploaded": 2})
        self.assertEqual({c[0] for c in self.calls}, {"t1", "t2"})
        self.assertEqual({u[0] for u in self.jf.uploads}, {"t1", "t2"})
        from studio import rules
        row = db.query_one("SELECT * FROM items WHERE jf_id = 't1'")
        self.assertEqual(rules.title_key(row), "stage:42")   # both recordings share one set of rules

    def test_stagemedia_wins_over_a_guessed_tmdb_id(self):
        # Jellyfin matched a show named like a film to the film (Beetlejuice → the 1988 movie).
        self.jf.items["lib-th"][0]["ProviderIds"]["Tmdb"] = "4288"
        prefs.set("stagemedia_key", "sm")
        self.assertEqual(self.sync(), {"uploaded": 2})
        self.assertEqual({c[0] for c in self.calls}, {"t1", "t2"})   # both drawn from StageMedia
        from studio import rules
        self.assertEqual(rules.title_key(db.query_one("SELECT * FROM items WHERE jf_id = 't1'")), "stage:42")

    def test_show_with_no_art_is_skipped_not_an_error(self):
        prefs.set("stagemedia_key", "sm")

        async def no_art(row, style, params, resolution):
            raise self.stage.NoArt("StageMedia has no poster for this show yet")
        self.stage.render = no_art
        self.assertEqual(self.sync(), {"skipped": 2})
        self.assertEqual(self.jf.uploads, [])
        self.assertNotIn(engine.ERROR, {r["status"] for r in db.query("SELECT status FROM items")})

    def test_empty_stagemedia_answer_means_no_posters(self):
        prefs.set("stagemedia_key", "sm")
        real = httpx.AsyncClient

        class Fake(real):
            def __init__(self, *a, **kw):
                kw["transport"] = httpx.MockTransport(
                    lambda r: httpx.Response(400, json={"posters": [], "performers": [], "error": "No actors"}))
                super().__init__(*a, **kw)
        self.stage.httpx.AsyncClient = Fake
        try:
            self.assertEqual(asyncio.run(self.stage.posters("9", force=True)), [])
        finally:
            self.stage.httpx.AsyncClient = real

    def test_show_name(self):
        self.assertEqual(self.stage.show_name("Hadestown - Broadway, 09-02-2024 - x"), "Hadestown")
        self.assertEqual(self.stage.show_name("Evita"), "Evita")
        self.assertTrue(self.stage.valid_poster("https://stagemedia.me/p/1.jpg"))
        self.assertFalse(self.stage.valid_poster("http://10.0.0.1/x.jpg"))


class SeasonTests(EngineTests):
    def setUp(self):
        super().setUp()
        from studio import seasons
        self.seasons = seasons
        self._orig = seasons.season_posters

        async def fake_posters(tmdb_id, number):
            if number == 1:
                return [{"path": "/season1.jpg", "language": None}, {"path": "/season1-en.jpg", "language": "en"}]
            return []
        seasons.season_posters = fake_posters

    def tearDown(self):
        self.seasons.season_posters = self._orig
        super().tearDown()

    def test_seasons_off_by_default(self):
        self.run_sync()
        self.assertFalse(db.query("SELECT 1 FROM items WHERE jf_type = 'Season'"))

    def test_season_posters(self):
        prefs.set("seasons_enabled", True)
        self.run_sync()
        seasons_rows = db.query("SELECT * FROM items WHERE jf_type = 'Season' ORDER BY season_number")
        self.assertEqual([(r["season_number"], r["parent_jf_id"], r["tmdb_id"]) for r in seasons_rows],
                         [(0, "s1", "70523"), (1, "s1", "70523")])
        from studio import rules
        self.assertEqual(rules.title_key(seasons_rows[1]), "tmdb:tv:70523:s1")
        season_reqs = [q for q in self.pp.requests if q.get("notch_label")]
        by_label = {q["notch_label"]: q for q in season_reqs}
        self.assertEqual(set(by_label), {"Specials", "Season 1"})
        self.assertEqual(by_label["Season 1"]["type"], "tv")
        self.assertEqual(by_label["Season 1"]["art_poster"], "/season1.jpg")       # textless first
        self.assertNotIn("art_poster", by_label["Specials"])                         # no season art: the show's
        # Never on the textless one moves to the titled one, served as it is.
        rules.set_never("tmdb:tv:70523:s1", "poster", "/season1.jpg", True)
        row = dict(seasons_rows[1])
        params = asyncio.run(self.seasons.params_for(row, rules.resolve("tmdb:tv:70523:s1").params))
        self.assertEqual((params["art_poster"], params["art_original"]), ("/season1-en.jpg", "1"))

    def test_unmatched_show_seasons_are_left_alone_until_the_show_is_matched(self):
        prefs.set("seasons_enabled", True)
        self.jf.find("s1")["ProviderIds"] = {}
        self.run_sync()
        st = {r["jf_id"]: r["status"] for r in db.query("SELECT jf_id, status FROM items")}
        self.assertEqual(st["s1"], engine.NEEDS_MATCH)                        # the show is flagged
        self.assertEqual((st["s1-0"], st["s1-1"]), (engine.LEFT_ALONE,) * 2)  # its seasons aren't
        db.execute("UPDATE items SET manual_tmdb_id = '70523', status = 'new' WHERE jf_id = 's1'")
        self.run_sync()
        row = db.query_one("SELECT * FROM items WHERE jf_id = 's1-1'")
        self.assertEqual((row["manual_tmdb_id"], row["status"]), ("70523", engine.NEW))   # managed now

    def test_season_inherits_show_style_but_can_override(self):
        from studio import rules
        rules.set_title_style("tmdb:tv:70523", {"tint_color": "112233", "bottom_gradient": "low"})
        rules.set_title_style("tmdb:tv:70523:s1", {"bottom_gradient": "off", "notch_label": "Book One"})
        row = {"jf_id": "s1-1", "jf_type": "Season", "tmdb_id": "70523", "season_number": 1}
        params = asyncio.run(self.seasons.params_for(row, rules.resolve("tmdb:tv:70523:s1").params))
        self.assertEqual(params["tint_color"], "112233")      # from the show
        self.assertEqual(params["bottom_gradient"], "off")    # the season's own
        self.assertEqual(params["notch_label"], "Book One")


class LooksTheSameTests(unittest.TestCase):
    """Compression noise isn't a change; a new label or poster is."""

    def _jpeg(self, img, q):
        import io
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=q)
        return out.getvalue()

    def _poster(self, label):
        from PIL import Image, ImageDraw, ImageFont
        img = Image.new("RGB", (1000, 1500))
        d = ImageDraw.Draw(img)
        for y in range(0, 1500, 6):   # busy art, so JPEG has something to get wrong
            d.line([(0, y), (1000, (y * 7) % 1500)], fill=((y * 3) % 255, (y * 5) % 255, 120), width=3)
        d.rectangle((330, 0, 670, 70), fill=(230, 230, 230))
        font = ImageFont.truetype(os.path.join(os.path.dirname(__file__), "..", "fonts", "Inter-Bold.ttf"), 40)
        d.text((350, 12), label, fill=(0, 0, 0), font=font)
        return img

    def test_noise_is_the_same_poster(self):
        img = self._poster("Trending #12")
        # One extra high-quality JPEG generation: what re-decoding the cached art does.
        a, b = self._jpeg(img, 92), self._jpeg(self._jpeg_roundtrip(img, 90), 92)
        self.assertNotEqual(a, b)
        self.assertTrue(engine.looks_the_same(a, b))

    def test_a_new_label_is_a_change(self):
        a = self._jpeg(self._poster("Trending #12"), 92)
        b = self._jpeg(self._poster("Trending #13"), 92)
        self.assertFalse(engine.looks_the_same(a, b))

    def test_undecodable_counts_as_different(self):
        self.assertFalse(engine.looks_the_same(b"nope", b"nope2"))

    def _jpeg_roundtrip(self, img, q):
        import io
        from PIL import Image
        return Image.open(io.BytesIO(self._jpeg(img, q))).convert("RGB")


class SmallPieces(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.connect(os.path.join(self.tmp.name, "studio.db"))

    def tearDown(self):
        db._conn.close()
        db._conn = None
        self.tmp.cleanup()

    def test_session_token(self):
        tok = auth._sign(2_000_000_000)
        self.assertTrue(auth.valid_token(tok, now=1_900_000_000))
        self.assertFalse(auth.valid_token(tok, now=2_000_000_001))            # expired
        self.assertFalse(auth.valid_token(tok[:-1] + ("0" if tok[-1] != "0" else "1"), now=1))
        self.assertFalse(auth.valid_token("2000000000.abc", now=1))
        self.assertFalse(auth.valid_token(None))

    def test_clean_style_drops_identity_and_keys(self):
        out = prefs.clean_style("http://x:8183/poster?tmdb_id=1&type=movie&access_key=s&sash_mode=notch&tmdb_key=z")
        self.assertEqual(out, "sash_mode=notch")
        with self.assertRaises(ValueError):
            prefs.clean_style("tmdb_id=1")

    def test_library_defaults(self):
        self.assertEqual(prefs.library_policy("a", "Concerts"), {"enabled": True, "unmatched": "leave"})
        self.assertEqual(prefs.library_policy("b", "Movies"), {"enabled": True, "unmatched": "flag"})
        self.assertEqual(prefs.library_policy("c", "Theatre"), {"enabled": False, "unmatched": "flag"})

    def test_quality_tokens(self):
        item = {"MediaSources": [{"Path": "/m/Film.2160p.REMUX.mkv", "MediaStreams": [
            {"Type": "Video", "Width": 3840, "Height": 1606, "VideoRangeType": "DOVIWithHDR10"},
            {"Type": "Audio", "DisplayTitle": "TrueHD Atmos 7.1"}]}]}
        self.assertEqual(quality_tokens(item), ["4K", "ATMOS", "DV", "REMUX"])
        self.assertEqual(quality_tokens({}), [])


if __name__ == "__main__":
    unittest.main()
