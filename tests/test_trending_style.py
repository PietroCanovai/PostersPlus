"""trending_style=number / ribbon draws the trending rank as its own mark, and
the sash moves on to the next label in the priority list."""
import unittest
from unittest import mock

import numpy as np
from PIL import Image

import discovery
import i18n
import main
import trending_rank


def _poster(w=500, h=750):
    return Image.new("RGBA", (w, h), (0, 0, 0, 0))


def _alpha_sum(img, box):
    return int(np.asarray(img.crop(box))[..., 3].sum())


class TrendingStyleConfigTests(unittest.TestCase):
    def test_default_is_the_sash_label(self):
        self.assertEqual(main.build_request_config({}).trending_style, "sash")

    def test_number_and_ribbon_parse(self):
        for style in ("number", "ribbon", "NUMBER"):
            with self.subTest(style=style):
                cfg = main.build_request_config({"trending_style": style})
                self.assertEqual(cfg.trending_style, style.lower())

    def test_unknown_value_keeps_the_default(self):
        self.assertEqual(main.build_request_config({"trending_style": "banner"}).trending_style, "sash")

    def test_default_leaves_the_cache_key_alone(self):
        sig = main._render_config_signature(main.build_request_config({}))
        self.assertNotIn("trending_style", sig)
        sig = main._render_config_signature(main.build_request_config({"trending_style": "ribbon"}))
        self.assertIn("trending_style", sig)


class ShownTrendingRankTests(unittest.TestCase):
    def setUp(self):
        for name, value in (("TRENDING_FETCH_COUNT", 40), ("TRENDING_BROAD_FETCH_COUNT", 100)):
            patcher = mock.patch.object(discovery._cfg, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _rank(self, rank, priority):
        return discovery.shown_trending_rank(discovery.DiscoveryMeta(trending_rank=rank), priority)

    def test_trending_slot_anywhere_in_the_list(self):
        # Below a slot that would win the sash: the mark is drawn regardless.
        self.assertEqual(self._rank(3, ["wins", "new_season", "trending"]), 3)

    def test_broad_ranks_need_the_broad_slot(self):
        self.assertIsNone(self._rank(60, ["trending"]))
        self.assertEqual(self._rank(60, ["trending_broad"]), 60)
        self.assertIsNone(self._rank(3, ["trending_broad"]))

    def test_nothing_without_a_trending_slot_or_rank(self):
        self.assertIsNone(self._rank(3, ["wins", "new_season"]))
        self.assertIsNone(self._rank(None, ["trending"]))
        self.assertIsNone(self._rank(101, ["trending", "trending_broad"]))


class RankMarkRenderingTests(unittest.TestCase):
    def test_number_sits_top_left_and_mirrors_right(self):
        left = trending_rank.draw_rank_number(_poster(), 7)
        right = trending_rank.draw_rank_number(_poster(), 7, right=True)
        self.assertGreater(_alpha_sum(left, (0, 0, 150, 150)), 0)
        self.assertEqual(_alpha_sum(left, (350, 0, 500, 150)), 0)
        self.assertGreater(_alpha_sum(right, (350, 0, 500, 150)), 0)
        self.assertEqual(_alpha_sum(right, (0, 0, 150, 150)), 0)
        # Nothing below the numeral's band but its shadow's fringe.
        self.assertEqual(_alpha_sum(left, (0, 200, 500, 750)), 0)

    def test_number_is_silver_to_white(self):
        art = Image.new("RGBA", (500, 750), (0, 0, 0, 255))
        out = np.asarray(trending_rank.draw_rank_number(art, 1)).astype(int)
        inset, bottom = trending_rank.number_box(500)
        head = out[inset + 4, :150, :3].max()
        # A few rows up: the box allows for the round digits' overshoot.
        foot = out[bottom - 10, :150, :3].max()
        self.assertGreater(head, foot)
        self.assertGreater(foot, 120)

    def test_number_shrinks_to_max_w(self):
        wide = trending_rank.draw_rank_number(_poster(), 24).getbbox()
        narrow = trending_rank.draw_rank_number(_poster(), 24, max_w=80).getbbox()
        self.assertLess(narrow[2] - narrow[0], wide[2] - wide[0])
        # Never below 60 % of its size, however little room there is.
        tiny = trending_rank.draw_rank_number(_poster(), 24, max_w=1).getbbox()
        self.assertGreater(tiny[3] - tiny[1], 0.5 * (wide[3] - wide[1]))

    def test_ribbon_hangs_from_the_top_edge_in_from_the_corner(self):
        out = trending_rank.draw_rank_ribbon(_poster(), 3)
        top_row = np.asarray(out)[0, :, 3]
        opaque = np.flatnonzero(top_row > 200)
        self.assertGreater(opaque.size, 0)
        self.assertGreaterEqual(opaque[0], round(trending_rank._RIB_INSET * 500) - 1)
        self.assertEqual(_alpha_sum(out, (250, 0, 500, 750)), 0)
        right = trending_rank.draw_rank_ribbon(_poster(), 3, right=True)
        self.assertEqual(_alpha_sum(right, (0, 0, 250, 750)), 0)

    def test_ribbon_widens_for_three_digits(self):
        two = trending_rank.draw_rank_ribbon(_poster(), 40)
        three = trending_rank.draw_rank_ribbon(_poster(), 100)
        row = lambda im: np.flatnonzero(np.asarray(im)[0, :, 3] > 200)
        self.assertGreater(len(row(three)), len(row(two)))


class RibbonOptionTests(unittest.TestCase):
    def _foot(self, im):
        # Lowest row with the ribbon's body in it (the shadow is fainter).
        rows = np.flatnonzero((np.asarray(im)[:, :250, 3] > 200).any(axis=1))
        return rows[-1]

    def test_label_lengthens_the_ribbon(self):
        plain = trending_rank.draw_rank_ribbon(_poster(), 3)
        labelled = trending_rank.draw_rank_ribbon(_poster(), 3, label="SERIES")
        self.assertGreater(self._foot(labelled), self._foot(plain))

    def test_long_label_stays_inside_the_ribbon(self):
        art = Image.new("RGBA", (500, 750), (0, 0, 0, 255))
        out = np.asarray(trending_rank.draw_rank_ribbon(art, 3, label="MFULULIZO")).astype(int)
        x0 = round(trending_rank._RIB_INSET * 500)
        x1 = x0 + round(trending_rank._RIB_W * 500)
        text = np.flatnonzero(out[:, :, :3].max(axis=(0, 2)) > 150)
        self.assertGreaterEqual(text[0], x0)
        self.assertLessEqual(text[-1], x1)

    def test_corner_nests_against_the_edge(self):
        for right in (False, True):
            with self.subTest(right=right):
                out = np.asarray(trending_rank.draw_rank_ribbon(_poster(), 3, right=right, corner=True))
                edge = out[5, -1 if right else 0, 3]
                self.assertGreater(edge, 200)
                inset = np.asarray(trending_rank.draw_rank_ribbon(_poster(), 3, right=right))
                self.assertLess(inset[5, -1 if right else 0, 3], 60)

    def test_scale_sizes_both_marks(self):
        for draw in (trending_rank.draw_rank_ribbon, trending_rank.draw_rank_number):
            with self.subTest(draw=draw.__name__):
                small = draw(_poster(), 8, scale=0.6).getbbox()
                big = draw(_poster(), 8, scale=1.5).getbbox()
                self.assertGreater(big[2] - big[0], 1.8 * (small[2] - small[0]))
                self.assertGreater(big[3], small[3])

    def test_config_parses_and_clamps(self):
        cfg = main.build_request_config({"trending_scale": "9", "trending_label": "true",
                                         "trending_corner": "1"})
        self.assertEqual(cfg.trending_scale, 2.0)
        self.assertTrue(cfg.trending_label and cfg.trending_corner)
        sig = main._render_config_signature(main.build_request_config({}))
        for name in ("trending_scale", "trending_label", "trending_corner"):
            self.assertNotIn(name, sig)

    def test_label_follows_kind_and_language(self):
        i18n.load_languages()
        cfg = main.build_request_config({"trending_style": "ribbon", "trending_label": "true"})
        seen = []
        real = trending_rank.draw_rank_ribbon
        with mock.patch.object(trending_rank, "draw_rank_ribbon",
                               side_effect=lambda *a, **kw: seen.append(kw["label"]) or real(*a, **kw)):
            for kind in ("movie", "series", "anime", None):
                main._draw_trending_rank(_poster(), cfg, 3, None, kind)
            cfg.logo_language = "de"
            main._draw_trending_rank(_poster(), cfg, 3, None, "series")
        self.assertEqual(seen, ["FILM", "SERIES", "ANIME", None, "SERIE"])


class RankSideTests(unittest.TestCase):
    def test_left_unless_the_sash_or_chip_is_there(self):
        cases = (
            ({}, False),
            ({"sash_side": "left"}, True),
            ({"sash_mode": "notch"}, False),
            ({"sash_mode": "notch", "sash_badge_pos": "left"}, True),
            ({"sash_mode": "notch", "sash_badge_pos": "right"}, False),
            ({"sash_mode": "hidden", "sash_side": "left"}, False),
        )
        for params, right in cases:
            with self.subTest(**params):
                self.assertEqual(main._rank_on_right(main.build_request_config(params)), right)

    def test_numeral_clears_what_the_sash_drew_beside_it(self):
        cfg = main.build_request_config({"trending_style": "number"})
        art = Image.new("RGBA", (500, 750), (0, 0, 0, 255))
        free = main._draw_trending_rank(art.copy(), cfg, 24, None)
        before = np.asarray(art)[:trending_rank.number_box(500)[1]].copy()
        # A block standing in for a notch, from x=150 across the top band.
        blocked = art.copy()
        blocked.paste((255, 255, 255, 255), (150, 0, 350, 60))
        out = main._draw_trending_rank(blocked, cfg, 24, before)
        lit = lambda im: np.flatnonzero(np.asarray(im)[40:120, :150, :3].max(axis=(0, 2)) > 100)
        self.assertLess(lit(out)[-1], lit(free)[-1])


if __name__ == "__main__":
    unittest.main()
