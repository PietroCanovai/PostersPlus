"""How a title is identified (Jellyfin's ids, or yours), and what that means
for the run: IMDb/TVDB-only titles, titles drawn from your own images."""
import asyncio
import json
import os
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit

import httpx

from studio import api_library, db, encora, engine, identity, prefs, rules
from test_studio_engine import FakeJellyfin


class Body:
    def __init__(self, data):
        self.data = data

    async def json(self):
        return self.data


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.connect(os.path.join(self.tmp.name, "studio.db"))
        prefs.set("jellyfin_url", "http://jf.test")
        prefs.set("jellyfin_api_key", "k")
        prefs.set("uploads_enabled", True)
        self.jf = FakeJellyfin()
        self.jf.items["lib-m"] += [
            {**self.jf._item("m3", "Movie", "Only on IMDb"), "ProviderIds": {"Imdb": "tt7654321"}},
        ]
        self.jf.items["lib-t"] += [
            {**self.jf._item("s2", "Series", "Only on TVDB"), "ProviderIds": {"Tvdb": "81189"}},
        ]
        self.requests = []
        from studio import stage
        self.stage, self._render = stage, stage.render
        self.own = []

        async def fake_render(row, style, params, resolution):
            self.own.append((row["jf_id"], params))
            return b"own-" + params["art_poster"].encode(), "image/jpeg"
        stage.render = fake_render
        self._find = identity.find_tmdb

    def tearDown(self):
        self.stage.render = self._render
        identity.find_tmdb = self._find
        db._conn.close()
        db._conn = None
        self.tmp.cleanup()

    def renderer(self, request):
        q = {k: v[0] for k, v in parse_qs(urlsplit(str(request.url)).query).items()}
        self.requests.append(q)
        name = q.get("tmdb_id") or q.get("imdb_id") or q.get("stremio_id")
        return httpx.Response(200, content=f"poster-{name}".encode(), headers={"content-type": "image/jpeg"})

    def sync(self, **kw):
        engine.progress.cancel = False
        rid = asyncio.run(engine.run(trigger="manual", dry_run=False,
                                     jf_transport=httpx.MockTransport(self.jf.handler),
                                     render_transport=httpx.MockTransport(self.renderer), **kw))
        return json.loads(db.query_one("SELECT counts FROM runs WHERE id = ?", (rid,))["counts"])

    def row(self, jf_id):
        return db.query_one("SELECT * FROM items WHERE jf_id = ?", (jf_id,))

    def match(self, jf_id, body):
        resp = asyncio.run(api_library.match(jf_id, Body(body)))
        return json.loads(resp.body)

    def test_imdb_and_tvdb_only_titles_are_drawn_without_tmdb(self):
        self.sync()
        by_id = {u[0]: u[1] for u in self.jf.uploads}
        self.assertEqual(by_id["m3"], b"poster-tt7654321")
        self.assertEqual(by_id["s2"], b"poster-tvdb:81189")
        imdb = next(q for q in self.requests if q.get("imdb_id") == "tt7654321")
        self.assertNotIn("tmdb_id", imdb)
        tvdb = next(q for q in self.requests if q.get("stremio_id"))
        self.assertEqual((tvdb["type"], "tmdb_id" in tvdb, "imdb_id" in tvdb), ("tv", False, False))
        self.assertEqual(self.row("m3")["status"], engine.OK)

    def test_a_title_no_database_knows_is_drawn_from_your_image(self):
        self.sync()
        self.assertEqual(self.row("m2")["status"], engine.NEEDS_MATCH)
        self.assertNotIn("m2", {u[0] for u in self.jf.uploads})
        # Pin an image of yours: it can be pushed at once, identified or not.
        look = rules.add_look("jf:m2", {"poster": "custom:aaaaaaaaaaaaaaaa.jpg"})
        rules.set_mode("jf:m2", "pinned", look["look_id"])
        self.assertEqual(self.sync(item_ids=["m2"]), {"uploaded": 1})
        self.assertEqual(self.jf.uploads[-1][:2], ("m2", b"own-custom:aaaaaaaaaaaaaaaa.jpg"))
        self.assertEqual(self.own[-1][0], "m2")
        # The next scan no longer lists it as needing attention, and nothing is re-sent.
        counts = self.sync()
        self.assertEqual(self.row("m2")["status"], engine.OK)
        self.assertEqual(counts.get("uploaded"), None)

    def test_none_is_a_choice_not_a_problem(self):
        self.sync()
        out = self.match("m2", {"source": "none"})
        self.assertEqual((out["identity"]["source"], out["identity"]["kind"]), ("none", "own"))
        self.assertEqual(self.row("m2")["status"], engine.NEW)
        self.sync()
        self.assertEqual(self.row("m2")["status"], engine.NEW)          # still nothing pinned: skipped, not flagged
        self.assertEqual(self.own, [])

    def test_identifying_keeps_what_you_did_before(self):
        self.sync()
        rules.add_look("jf:m2", {"poster": "custom:aaaaaaaaaaaaaaaa.jpg"})
        out = self.match("m2", {"source": "tmdb", "id": "https://www.themoviedb.org/movie/603-the-matrix"})
        self.assertTrue(out["moved_rules"])
        self.assertEqual(out["title_key"], "tmdb:movie:603")
        self.assertEqual(len(rules.looks("tmdb:movie:603")), 1)
        self.assertEqual(rules.looks("jf:m2"), [])
        self.sync(item_ids=["m2"])
        self.assertEqual(self.requests[-1]["tmdb_id"], "603")
        # Back to Jellyfin's ids: unidentified again.
        out = self.match("m2", {"source": "auto"})
        self.assertEqual(out["identity"]["kind"], "own")

    def test_imdb_id_resolves_to_tmdb_when_tmdb_lists_it(self):
        self.sync()

        async def found(src, ext, media):
            return {"tmdb_id": "949", "title": "Heat", "year": "1995"} if ext == "tt0113277" else None
        identity.find_tmdb = found
        out = self.match("m2", {"source": "imdb", "id": "https://www.imdb.com/title/tt0113277/"})
        self.assertEqual(out["linked"], "Linked to Heat (1995)")
        self.assertEqual((out["identity"]["tmdb_id"], out["identity"]["imdb_id"]), ("949", "tt0113277"))
        self.assertEqual(out["title_key"], "tmdb:movie:949")           # shares rules with the other copy of Heat
        out = self.match("m2", {"source": "imdb", "id": "tt9999999"})
        self.assertEqual((out["identity"]["tmdb_id"], out["identity"]["imdb_id"]), (None, "tt9999999"))
        self.assertEqual(identity.render_ids(self.row("m2")), {"imdb_id": "tt9999999"})
        with self.assertRaises(Exception):
            self.match("m2", {"source": "imdb", "id": "heat"})

    def test_stagemedia_by_show_id_or_encora_recording(self):
        self.sync()
        out = self.match("m2", {"source": "stage", "id": "42"})
        self.assertEqual((out["identity"]["kind"], out["identity"]["stage_id"], out["title_key"]),
                         ("stage", "42", "stage:42"))
        self.assertTrue(out["stage"])

        async def rec(recording_id, **kw):
            return {"show_id": "77", "show": "Hadestown", "tour": "Broadway"}
        orig, encora.recording = encora.recording, rec
        try:
            out = self.match("m2", {"source": "stage", "recording": "https://encora.it/recordings/12345"})
        finally:
            encora.recording = orig
        self.assertEqual(out["title_key"], "stage:77")
        self.assertIn("Hadestown", out["linked"])

    def test_encora_client(self):
        prefs.set("encora_key", "enc")
        seen = []

        def handler(request):
            seen.append((request.url.path, request.headers["authorization"]))
            if request.url.path == "/api/shows/search":
                return httpx.Response(200, json=[{"id": 42, "name": "Hadestown", "year": 2019, "poster_url": None}])
            return httpx.Response(200, json={"id": 5, "show": "Hadestown", "tour": "Broadway",
                                             "metadata": {"show_id": 42}})
        t = httpx.MockTransport(handler)
        self.assertEqual(asyncio.run(encora.search_shows("hades", transport=t))[0]["id"], "42")
        self.assertEqual(asyncio.run(encora.recording("5", transport=t))["show_id"], "42")
        self.assertEqual(seen, [("/api/shows/search", "Bearer enc"), ("/api/recording/5", "Bearer enc")])

    def test_images_come_from_every_provider_the_ids_reach(self):
        """A film only IMDb knows: no TMDB id, yet TVDB, Fanart and IMDb all offer art."""
        import sys
        import types
        from studio import artwork, candidates
        asked = {}

        async def resolve_tvdb_id(client, *, media_type, tvdb_id_hint=None, imdb_id=None, tmdb_id=None):
            asked["tvdb"] = (tvdb_id_hint, imdb_id, tmdb_id)
            return 555

        async def fetch_tvdb_artworks(client, tvdb_id, media_type):
            return {"posters": [{"url": "https://artworks.thetvdb.com/p.jpg", "language": "eng", "score": 1}],
                    "logos": [], "backgrounds": [{"url": "https://artworks.thetvdb.com/b.jpg", "language": None}]}

        async def fanart_candidates(client, *, media_type, tmdb_id, imdb_id=None):
            asked["fanart"] = tmdb_id
            return {"posters": [{"path": "https://assets.fanart.tv/fanart/x.jpg", "language": None}],
                    "logos": [], "backdrops": []}

        async def probe_art(client, imdb_id):
            return True, True

        async def head_ok(client, url):
            return True
        fakes = {
            "main": types.SimpleNamespace(_HTTP_CLIENT=object()),
            "config": types.SimpleNamespace(SERVER_TMDB_KEY="", FANART_API_KEY="f"),
            "tvdb": types.SimpleNamespace(tvdb_enabled=lambda: True, resolve_tvdb_id=resolve_tvdb_id,
                                          fetch_tvdb_artworks=fetch_tvdb_artworks, _LANG_3_TO_2={"eng": "en"}),
            "fanart": types.SimpleNamespace(artwork_candidates=fanart_candidates),
            "cinemeta": types.SimpleNamespace(
                METAHUB_BASE="https://images.metahub.space", probe_art=probe_art, _head_ok=head_ok,
                poster_url=lambda i, size="medium": f"https://images.metahub.space/poster/{size}/{i}/img",
                background_url=lambda i, size="large": f"https://images.metahub.space/background/{size}/{i}/img"),
        }
        saved = {k: sys.modules.get(k) for k in fakes}
        sys.modules.update(fakes)
        try:
            out = asyncio.run(candidates.for_title("movie", None, imdb_id="tt7654321", force=True))["candidates"]
        finally:
            for k, v in saved.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v
        self.assertEqual(asked, {"tvdb": (None, "tt7654321", None), "fanart": "tt7654321"})
        self.assertEqual([c["provider"] for c in out["posters"]], ["fanart", "tvdb", "imdb"])   # textless first
        self.assertEqual({c["provider"] for c in out["backdrops"]}, {"tvdb", "imdb"})
        self.assertEqual([c["provider"] for c in out["logos"]], ["imdb"])
        # IMDb's images aren't a host the renderer fetches: they pass validation and are used through a copy.
        imdb_poster = next(c["path"] for c in out["posters"] if c["provider"] == "imdb")
        self.assertTrue(artwork.is_remote(imdb_poster) and artwork.is_remote(out["logos"][0]["path"]))
        self.assertFalse(artwork.is_remote("https://images.metahub.space/poster/large/tt1/img?x=1"))
        self.assertFalse(artwork.is_remote("https://evil.example/poster/large/tt1/img"))

    def test_parse_id(self):
        self.assertEqual(identity.parse_id("tvdb", "81189"), "81189")
        self.assertEqual(identity.parse_id("tmdb", " 949 "), "949")
        self.assertEqual(identity.parse_id("stage", "https://encora.it/shows/42"), "42")
        for src, bad in (("tmdb", "tt1"), ("tvdb", "abc"), ("imdb", "949")):
            with self.assertRaises(ValueError):
                identity.parse_id(src, bad)


if __name__ == "__main__":
    unittest.main()
