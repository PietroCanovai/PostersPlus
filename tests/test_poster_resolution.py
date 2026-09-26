"""resolution=780 renders portrait posters on a 780x1170 canvas from larger art,
with the fixed-pixel settings scaled to match, without disturbing 500x750."""
import contextvars
import unittest

from PIL import Image

import main
import tmdb


class ResolutionParamTests(unittest.TestCase):
    def test_default_is_500(self):
        self.assertEqual(main.build_request_config({}).poster_width, 500)

    def test_780_and_its_aliases(self):
        for raw in ("780", "high", "HD"):
            with self.subTest(raw=raw):
                self.assertEqual(main.build_request_config({"resolution": raw}).poster_width, 780)

    def test_unknown_values_keep_the_default(self):
        for raw in ("1000", "max", "", "{resolution}"):
            with self.subTest(raw=raw):
                self.assertEqual(main.build_request_config({"resolution": raw}).poster_width, 500)

    def test_landscape_ignores_it(self):
        cfg = main.build_request_config({"resolution": "780", "shape": "landscape"})
        self.assertEqual(cfg.poster_width, 500)


class SignatureTests(unittest.TestCase):
    def test_default_size_leaves_existing_keys_unchanged(self):
        # Composites cached before the option existed must keep their keys.
        sig = main._render_config_signature(main.build_request_config({}))
        self.assertNotIn("poster_width", sig)

    def test_large_size_gets_its_own_key(self):
        small = main._render_config_signature(main.build_request_config({}))
        large = main._render_config_signature(main.build_request_config({"resolution": "780"}))
        self.assertNotEqual(small, large)


class ScaleRenderCfgTests(unittest.TestCase):
    def test_500_is_untouched(self):
        cfg = main.build_request_config({})
        self.assertIs(main._scale_render_cfg(cfg), cfg)

    def test_780_scales_the_pixel_settings(self):
        cfg = main.build_request_config({"resolution": "780", "badge_height": "20",
                                         "badge_gap": "10", "score_glow_blur": "5"})
        scaled = main._scale_render_cfg(cfg)
        self.assertEqual((scaled.badge_height, scaled.badge_gap, scaled.score_glow_blur), (31, 16, 8))
        # The key is built from the unscaled config, so the scaled copy is new.
        self.assertEqual(cfg.badge_height, 20)


class CanvasTests(unittest.TestCase):
    def _in_request(self, fn):
        """Run fn with a 780 canvas set, as get_poster does, without leaking it."""
        def body():
            tmdb.set_poster_canvas(780)
            return fn()
        return contextvars.copy_context().run(body)

    def test_normalise_follows_the_request_canvas(self):
        art = Image.new("RGBA", (780, 1170))
        self.assertEqual(tmdb.normalise_poster(art).size, (500, 750))
        self.assertEqual(self._in_request(lambda: tmdb.normalise_poster(art)).size, (780, 1170))

    def test_explicit_size_wins(self):
        # The thread-pool crop passes its size explicitly.
        art = Image.new("RGBA", (400, 600))
        self.assertEqual(tmdb.normalise_poster(art, (780, 1170)).size, (780, 1170))

    def test_art_cache_keys_are_per_size(self):
        small = tmdb.poster_image_cache_key("603", "movie", "/a.jpg")
        large = self._in_request(lambda: tmdb.poster_image_cache_key("603", "movie", "/a.jpg"))
        self.assertNotEqual(small, large)
        self.assertTrue(large.startswith(small))
        bd_small = tmdb.backdrop_image_cache_key("603", "/b.jpg", False)
        bd_large = self._in_request(lambda: tmdb.backdrop_image_cache_key("603", "/b.jpg", False))
        self.assertNotEqual(bd_small, bd_large)
        self.assertTrue(bd_large.startswith(bd_small))

    def test_fallback_canvas_follows_the_request_canvas(self):
        self.assertEqual(main._make_fallback_canvas(None).size, (500, 750))
        self.assertEqual(self._in_request(lambda: main._make_fallback_canvas(None)).size, (780, 1170))


if __name__ == "__main__":
    unittest.main()
