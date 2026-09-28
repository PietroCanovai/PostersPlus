"""TMDB files TV under one "Sci-Fi & Fantasy" genre (10765).  The show's
keywords — and, on a tie, IMDb's genres via Cinemeta — split it into the film
ids 878 (Sci-Fi) or 14 (Fantasy); a show nothing decides keeps 10765."""
import asyncio
import os
import tempfile
import unittest
from unittest import mock

import cache
import tmdb
from tmdb import _scifi_or_fantasy, _split_tv_scifi_fantasy


class ClassifierTests(unittest.TestCase):
    def test_keywords_decide(self):
        self.assertEqual(_scifi_or_fantasy(["dragon", "fantasy world"]), 14)
        self.assertEqual(_scifi_or_fantasy(["android", "robot", "artificial intelligence (a.i.)"]), 878)
        self.assertEqual(_scifi_or_fantasy(["space opera", "space western"]), 878)
        self.assertEqual(_scifi_or_fantasy(["witchcraft", "dark fantasy"]), 14)

    def test_terms_match_whole_words(self):
        # "los angeles" is not an angel, "alienation" not an alien.
        self.assertIsNone(_scifi_or_fantasy(["los angeles, california"]))
        self.assertIsNone(_scifi_or_fantasy(["alienation"]))
        self.assertEqual(_scifi_or_fantasy(["dystopian future"]), 878)
        self.assertEqual(_scifi_or_fantasy(["vampires"]), 14)

    def test_imdb_genres_only_break_a_tie(self):
        self.assertEqual(_scifi_or_fantasy([], ["Drama", "Mystery", "Sci-Fi"]), 878)
        self.assertEqual(_scifi_or_fantasy(["time travel", "magic"], ["Fantasy"]), 14)
        self.assertEqual(_scifi_or_fantasy(["robot"], ["Fantasy"]), 878)
        self.assertIsNone(_scifi_or_fantasy([], ["Drama", "Fantasy", "Sci-Fi"]))
        self.assertIsNone(_scifi_or_fantasy([], ["Action", "Adventure", "Drama"]))


class SplitTests(unittest.TestCase):
    def _split(self, ids, keywords, imdb_id=None, cinemeta=None):
        async def fake_meta(client, imdb, media_type):
            return cinemeta
        with mock.patch.object(tmdb.cinemeta, "fetch_cinemeta_meta", side_effect=fake_meta) as m, \
             mock.patch.object(tmdb, "CINEMETA_ENABLED", True):
            out = asyncio.run(_split_tv_scifi_fantasy(None, ids, keywords, imdb_id))
        return out, m.call_count

    def test_replaces_in_place_and_dedupes(self):
        out, calls = self._split([10765, 18, 10759], ["fantasy world"])
        self.assertEqual(out, [14, 18, 10759])
        self.assertEqual(calls, 0)
        out, _ = self._split([878, 10765], ["alien"])
        self.assertEqual(out, [878])

    def test_untouched_without_10765(self):
        out, calls = self._split([18, 80], ["dragon"], "tt1")
        self.assertEqual((out, calls), ([18, 80], 0))

    def test_cinemeta_only_on_a_tie(self):
        out, calls = self._split([10765, 18], [], "tt11280740",
                                 {"genres": ["Drama", "Mystery", "Sci-Fi"]})
        self.assertEqual((out, calls), ([878, 18], 1))

    def test_unresolved_keeps_10765(self):
        out, _ = self._split([10765, 18], [], "tt1", {"genres": ["Drama"]})
        self.assertEqual(out, [10765, 18])
        out, _ = self._split([10765], [], "tt1", None)
        self.assertEqual(out, [10765])


class CachedRowRefreshTests(unittest.TestCase):
    """v4 rows are kept, except a TV row still carrying the merged genre."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self._saved = (cache.DB_PATH, cache._initialised, getattr(cache._local, "conn", None))
        cache.DB_PATH = os.path.join(self._dir.name, "cache.db")
        cache._local.conn = None
        cache.init_db()

    def tearDown(self):
        conn = getattr(cache._local, "conn", None)
        if conn is not None:
            conn.close()
        cache.DB_PATH, cache._initialised, cache._local.conn = self._saved
        self._dir.cleanup()

    def _store(self, key, genre_ids, version):
        cache.set_cached_tmdb_metadata(
            key, "T", "2020", genre_ids, True, "/p.jpg", [],
            original_title="T", vote_count=10, metadata_version=version,
        )

    def test_only_merged_tv_rows_refresh(self):
        self._store("tv_1_en_x", [10765, 18], 4)
        self._store("tv_2_en_x", [18], 4)
        self._store("movie_3_en_x", [10765], 4)
        self._store("tv_4_en_x", [10765], 5)
        with mock.patch.object(cache, "invalidate_final_posters") as inv:
            self.assertIsNone(cache.get_cached_tmdb_metadata("tv_1_en_x"))
            inv.assert_called_once_with("1", "tv")
        for key in ("tv_2_en_x", "movie_3_en_x", "tv_4_en_x"):
            self.assertIsNotNone(cache.get_cached_tmdb_metadata(key), key)


class GenreOrderSettingTests(unittest.TestCase):
    """The dashboard's drag list stores a full ranking of known ids."""

    def setUp(self):
        import settings
        self.settings = settings
        self.s = settings.REGISTRY["GENRE_PRIORITY"]

    def test_declared_as_an_order_of_every_genre(self):
        import config
        self.assertEqual(self.s.kind, "order")
        self.assertEqual({int(c) for c in self.s.choices}, set(config.GENRE_MAP))
        self.assertEqual(self.s.labels["10765"], "Sci-Fi & Fantasy (TV, not split)")

    def test_missing_ids_keep_their_default_place_at_the_end(self):
        out = self.settings.normalise(self.s, "14, 878")
        parts = out.split(",")
        self.assertEqual(parts[:2], ["14", "878"])
        self.assertEqual(sorted(parts), sorted(self.s.choices))

    def test_unknown_or_repeated_ids_are_refused(self):
        with self.assertRaises(ValueError):
            self.settings.normalise(self.s, "14,99999")
        with self.assertRaises(ValueError):
            self.settings.normalise(self.s, "14,14")

    def test_environment_value_is_parsed_leniently(self):
        import config
        with mock.patch.dict(os.environ, {"X_ORDER": "14,junk,14,878"}):
            order = config._genre_order("X_ORDER", (878, 14, 18), "x", "")
        self.assertEqual(order, [14, 878, 18])


if __name__ == "__main__":
    unittest.main()
