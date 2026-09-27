"""Rating provider badges (rating_badges=): each chosen provider's own score
behind its mark, in place of the ★ and the weighted score, in the Clean,
Minimalist and Bar modes."""
import asyncio
import hashlib
import os
import tempfile
import unittest
from unittest import mock

import numpy as np
from PIL import Image

import main
import rating_badges as rb

def _svg(key: str) -> bytes:
    """A plain square mark standing in for *key*'s pinned file."""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40"><!-- {key} -->'
            f'<rect width="40" height="40" fill="#e0301e"/></svg>').encode()

_DUNE = {"imdb": 8.4, "tomatoes": 92, "popcorn": 95, "letterboxd": 4.4,
         "metacritic": 79, "metacriticuser": 8.3, "trakt": 86, "tmdb": 81, "rogerebert": 3.5}


class _Resp:
    def __init__(self, content, status=200):
        self.content, self.status_code = content, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class _Client:
    """Serves each pinned file's stand-in, or *body* for every URL."""
    def __init__(self, body=None):
        self.body, self.urls = body, []

    async def get(self, url, **_kw):
        self.urls.append(url)
        if self.body is not None:
            return _Resp(self.body)
        return _Resp(_svg(next(k for k, f in rb._FILES.items() if f.url == url)))


class _AssetDir(unittest.TestCase):
    """Marks read from (and fetched into) a temporary directory."""
    pinned = True

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        patches = [mock.patch.object(rb, "ASSET_DIR", self.dir.name), mock.patch.dict(rb._failed_at, clear=True)]
        if self.pinned:
            patches.append(mock.patch.dict(rb._FILES, {
                k: rb._Source(f.url, hashlib.sha1(_svg(k)).hexdigest(), ".svg") for k, f in rb._FILES.items()}))
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        for fn in (rb._mark_rgba, rb.badge):
            fn.cache_clear()
            self.addCleanup(fn.cache_clear)

    def install_marks(self):
        for key in rb._FILES:
            with open(rb._asset_path(key), "wb") as fh:
                fh.write(_svg(key))


class ParseTests(unittest.TestCase):
    def test_known_providers_in_order_first_mention_kept(self):
        self.assertEqual(rb.parse_providers(" IMDb, bogus,tomatoes,imdb ,kitsu"), "imdb,tomatoes,kitsu")
        self.assertEqual(rb.parse_providers(None), "")
        self.assertEqual(rb.parse_providers("bogus"), "")

    def test_capped(self):
        self.assertEqual(len(rb.parse_providers(",".join(rb.PROVIDERS)).split(",")), rb._MAX_BADGES)

    def test_request_config(self):
        cfg = main.build_request_config({"rating_badges": "letterboxd,imdb", "rating_badge_scale": "normalized"})
        self.assertEqual((cfg.rating_badges, cfg.rating_badge_scale), ("letterboxd,imdb", "normalized"))
        cfg = main.build_request_config({"rating_badge_scale": "weird"})
        self.assertEqual((cfg.rating_badges, cfg.rating_badge_scale), ("", "native"))

    def test_defaults_leave_the_composite_signature_alone(self):
        # Every poster cached before badges existed keeps its key.
        sig = main._render_config_signature(main.build_request_config({}))
        self.assertNotIn("rating_badge", sig)
        self.assertIn("rating_badges", main._render_config_signature(
            main.build_request_config({"rating_badges": "imdb"})))


class ScoreTests(unittest.TestCase):
    def test_entries_follow_the_chosen_order_and_skip_missing(self):
        self.assertEqual(rb.entries({"imdb": 8.4, "tomatoes": 92, "trakt": None, "kitsu": True},
                                    "tomatoes,letterboxd,imdb,trakt,kitsu"),
                         [("tomatoes", 92.0), ("imdb", 8.4)])
        self.assertEqual(rb.entries(None, "imdb"), [])
        self.assertEqual(rb.entries({"imdb": 8.4}, ""), [])

    def test_native(self):
        cases = {"imdb": (8.4, "8.4"), "tomatoes": (92, "92%"), "popcorn": (58, "58%"),
                 "letterboxd": (4.4, "4.4"), "metacritic": (79, "79"), "metacriticuser": (8.3, "8.3"),
                 "trakt": (86, "86%"), "tmdb": (81.4, "81%"), "rogerebert": (3.5, "3.5"),
                 "myanimelist": (8.8, "8.8"), "anilist": (84, "84%"), "kitsu": (82.1, "82%")}
        for provider, (value, text) in cases.items():
            self.assertEqual(rb.score_text(provider, value, "native", True), text, provider)

    def test_normalized_follows_out_of_10(self):
        self.assertEqual(rb.score_text("letterboxd", 4.4, "normalized", False), "88")
        self.assertEqual(rb.score_text("letterboxd", 4.4, "normalized", True), "8.8")
        self.assertEqual(rb.score_text("tomatoes", 100, "normalized", True), "10")

    def test_rotten_tomatoes_state_at_60(self):
        self.assertEqual(rb._mark_key("tomatoes", 60), "rt_fresh")
        self.assertEqual(rb._mark_key("tomatoes", 59), "rt_rotten")
        self.assertEqual(rb._mark_key("popcorn", 60), "rt_upright")
        self.assertEqual(rb._mark_key("popcorn", 59), "rt_spilled")


class FetchTests(_AssetDir):
    def test_only_the_needed_marks_are_fetched_and_kept(self):
        client = _Client()
        self.assertTrue(asyncio.run(rb.ensure_assets(client, ["imdb", "tomatoes", "rogerebert"])))
        self.assertEqual(len(os.listdir(self.dir.name)), 4)
        self.assertEqual(len(client.urls), 4)   # imdb, fresh, rotten, Ebert's thumb
        self.assertTrue(asyncio.run(rb.ensure_assets(client, ["imdb"])))
        self.assertEqual(len(client.urls), 4)


class FetchMismatchTests(_AssetDir):
    pinned = False

    def test_a_changed_file_is_not_used_nor_asked_for_again_straight_away(self):
        client = _Client(b"not the pinned file")
        self.assertFalse(asyncio.run(rb.ensure_assets(client, ["imdb"])))
        self.assertFalse(asyncio.run(rb.ensure_assets(client, ["imdb"])))
        self.assertEqual(os.listdir(self.dir.name), [])
        self.assertEqual(len(client.urls), 1)


class RunTests(_AssetDir):
    def test_every_badge_is_a_round_or_square_mark_filling_the_row(self):
        self.install_marks()
        for provider in rb.PROVIDERS:
            with self.subTest(provider=provider):
                im = rb.badge(provider, True, 20)
                self.assertEqual(im.height, 20)
                self.assertEqual(im.width, 20)

    def test_plated_marks_leave_their_corners_clear(self):
        # A disc: nothing in the corners of its square.
        self.install_marks()
        for key in ("imdb", "tmdb", "letterboxd", "trakt", "myanimelist", "anilist", "kitsu", "thumb_up"):
            with self.subTest(key=key):
                a = np.asarray(rb._mark_rgba(key))[..., 3]
                self.assertEqual(int(a[:8, :8].max()), 0)
                self.assertEqual(int(a[a.shape[0] // 2, a.shape[1] // 2]), 255)

    def test_a_missing_mark_keeps_its_score(self):
        run = rb.rating_run([("imdb", 8.4)], 20, "native", False)
        self.assertEqual(run, [("text", "8.4")])


class RenderTests(_AssetDir):
    def setUp(self):
        super().setUp()
        self.install_marks()

    def render(self, ratings=_DUNE, **kw):
        cfg = main.RequestConfig()
        for k, v in kw.items():
            setattr(cfg, k, v)
        art = Image.new("RGBA", (500, 750), (40, 60, 90, 255))
        return np.asarray(main.build_poster(art, 89, "Drama", cfg, release_year="2024", ratings=ratings))

    def red(self, im):
        """Pixels of the stand-in mark's colour."""
        r, g, b = im[..., 0].astype(int), im[..., 1].astype(int), im[..., 2].astype(int)
        return int(((r > 190) & (g < 80) & (b < 60)).sum())

    def test_each_mode_draws_badges_in_place_of_the_star(self):
        for mode, extra in ((2, {}), (3, {"minimalist_append_mode": 2}), (3, {"minimalist_append_mode": 3}),
                            (4, {}), (4, {"bar_append": "rating"})):
            with self.subTest(mode=mode, **extra):
                plain = self.render(rating_display_mode=mode, **extra)
                badged = self.render(rating_display_mode=mode, rating_badges="imdb,tomatoes", **extra)
                self.assertEqual(self.red(plain), 0)
                self.assertGreater(self.red(badged), 0)

    def test_the_bar_keeps_its_label_and_badges_take_the_rest(self):
        badges = "imdb,tomatoes,popcorn,letterboxd,trakt,tmdb"
        with_label = self.render(rating_display_mode=4, rating_badges=badges)
        bare = self.render(rating_display_mode=4, rating_badges=badges, hide_year=True, hide_genre=True)
        # Fewer fit beside "2024 · Drama · " than on a bar with nothing else.
        self.assertGreater(self.red(with_label), 0)
        self.assertGreater(self.red(bare), self.red(with_label))
        # ...and they follow the label rather than starting at the left edge.
        cols = np.flatnonzero(((with_label[..., 0] > 190) & (with_label[..., 1] < 80)).any(axis=0))
        self.assertGreater(cols[0], 150)

    def test_no_chosen_score_keeps_the_weighted_one(self):
        for mode in (2, 3, 4):
            with self.subTest(mode=mode):
                plain = self.render(rating_display_mode=mode, minimalist_append_mode=1)
                badged = self.render({"trakt": 86}, rating_display_mode=mode, minimalist_append_mode=1,
                                     rating_badges="imdb")
                np.testing.assert_array_equal(plain, badged)

    def test_modes_without_a_printed_score_are_untouched(self):
        cases = ({"rating_display_mode": 1},                                   # Rating Bar
                 {"rating_display_mode": 3, "minimalist_append_mode": 0},      # Minimalist Year
                 {"rating_display_mode": 4, "bar_append": "year"},
                 {"rating_display_mode": 2, "hide_rating": True})
        for kw in cases:
            with self.subTest(**kw):
                np.testing.assert_array_equal(self.render(**kw), self.render(rating_badges="imdb", **kw))

    def test_badges_that_do_not_fit_are_dropped_from_the_end(self):
        kw = {"rating_display_mode": 2, "numeric_score_font_size_ratio": 0.05}
        one = self.render(rating_badges="tomatoes", **kw)
        many = self.render(rating_badges="tomatoes,imdb,popcorn,letterboxd,trakt,tmdb", **kw)
        # More than one fits at this size, not all six; whatever is drawn stays on the poster.
        self.assertGreater(self.red(many), self.red(one))
        row = np.flatnonzero(((many[..., 0] > 190) & (many[..., 1] < 80)).any(axis=0))
        self.assertGreater(row[0], 0)
        self.assertLess(row[-1], 499)


if __name__ == "__main__":
    unittest.main()
