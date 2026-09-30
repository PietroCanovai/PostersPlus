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
            {"Name": "Concerts", "ItemId": "lib-c", "CollectionType": "movies"},
            {"Name": "Music", "ItemId": "lib-x", "CollectionType": "music"},
        ]
        self.items = {
            "lib-m": [self._item("m1", "Movie", "Heat", tmdb="949", imdb="tt0113277"),
                      self._item("m2", "Movie", "Unknown Home Video")],
            "lib-t": [self._item("s1", "Series", "Dark", tmdb="70523")],
            "lib-c": [self._item("c1", "Video", "Live at Wembley (YouTube)")],
            "lib-x": [],
        }
        self.uploads: list[tuple[str, bytes, str]] = []
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
            rows = self.items.get(q.get("ParentId"), [])
            start = int(q.get("StartIndex", 0))
            return httpx.Response(200, json={"Items": rows[start:], "TotalRecordCount": len(rows)})
        if path.endswith("/Images/Primary") and request.method == "POST":
            id_ = path.split("/")[2]
            self.uploads.append((id_, base64.b64decode(request.content), request.headers["content-type"]))
            self._tag += 1
            self.find(id_)["ImageTags"]["Primary"] = f"ours-{self._tag}"
            return httpx.Response(204)
        return httpx.Response(404)


class FakeRenderer:
    def __init__(self):
        self.version = {"949": b"heat-v1", "70523": b"dark-v1"}
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
        self.assertEqual(self.counts(run).get("would_upload"), 2)
        self.assertEqual(self.jf.uploads, [])

    def test_first_upload_then_unchanged(self):
        prefs.set("uploads_enabled", True)
        run = self.run_sync()
        self.assertEqual(run["status"], "done")
        self.assertEqual(self.counts(run), {"uploaded": 2, "skipped": 2})
        self.assertEqual({u[0] for u in self.jf.uploads}, {"m1", "s1"})
        self.assertTrue(all(u[2] == "image/jpeg" for u in self.jf.uploads))
        self.assertEqual(self.jf.uploads[0][1] in (b"heat-v1", b"dark-v1"), True)
        # Nothing changed: the second run uploads nothing.
        run = self.run_sync()
        self.assertEqual(self.counts(run), {"unchanged": 2, "skipped": 2})
        self.assertEqual(len(self.jf.uploads), 2)

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
