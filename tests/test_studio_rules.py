"""Studio's per-title rules: looks, Never lists, hands off, and the rotation deck."""
import os
import random
import tempfile
import unittest
from datetime import date, timedelta

from studio import db, rules

KEY = "tmdb:movie:949"


class RulesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.connect(os.path.join(self.tmp.name, "studio.db"))

    def tearDown(self):
        db._conn.close()
        db._conn = None
        self.tmp.cleanup()

    def test_title_keys(self):
        self.assertEqual(rules.title_key({"jf_id": "a", "tmdb_id": "1", "jf_type": "Movie"}), "tmdb:movie:1")
        self.assertEqual(rules.title_key({"jf_id": "a", "tmdb_id": "1", "jf_type": "Series"}), "tmdb:tv:1")
        self.assertEqual(rules.title_key({"jf_id": "a", "tmdb_id": "1", "manual_tmdb_id": "7", "jf_type": "Movie"}),
                         "tmdb:movie:7")
        self.assertEqual(rules.title_key({"jf_id": "a", "tmdb_id": None, "jf_type": "Movie"}), "jf:a")

    def test_auto_has_no_params(self):
        self.assertEqual(rules.resolve(KEY).params, {})

    def test_pinned_look_params(self):
        look = rules.add_look(KEY, {"poster": "/p.jpg", "logo": "/l.png",
                                    "colors": {"tint": "#AA0011", "logo": "ffffff", "logo_mode": "tint"},
                                    "style": {"logo_max_w_ratio": "0.6"}})
        rules.set_mode(KEY, "pinned", look["look_id"])
        p = rules.resolve(KEY).params
        self.assertEqual(p["art_poster"], "/p.jpg")
        self.assertEqual(p["art_logo"], "/l.png")
        self.assertEqual(p["tint_color"], "aa0011")
        self.assertEqual(p["logo_color"], "ffffff")
        self.assertEqual(p["logo_color_mode"], "tint")
        self.assertEqual(p["logo_max_w_ratio"], "0.6")

    def test_cropped_backdrop_and_own_title(self):
        look = rules.add_look(KEY, {"poster": "/b.jpg", "crop": {"x": 0.25, "y": 0.5, "zoom": 1.5}})
        rules.set_mode(KEY, "pinned", look["look_id"])
        self.assertEqual(rules.resolve(KEY).params["art_crop"], "0.2500,0.5000,1.500")
        look2 = rules.add_look(KEY, {"poster": "/t.jpg", "own_title": True})
        rules.set_mode(KEY, "pinned", look2["look_id"])
        self.assertEqual(rules.resolve(KEY).params["art_original"], "1")

    def test_title_style_under_look_style(self):
        rules.set_title_style(KEY, {"logo_max_w_ratio": "0.5", "bottom_gradient": "low", "tint_color": "112233"})
        self.assertEqual(rules.resolve(KEY).params,
                         {"logo_max_w_ratio": "0.5", "bottom_gradient": "low", "tint_color": "112233"})
        look = rules.add_look(KEY, {"poster": "/p.jpg", "style": {"logo_max_w_ratio": "0.9"}})
        rules.set_mode(KEY, "pinned", look["look_id"])
        p = rules.resolve(KEY).params
        self.assertEqual(p["logo_max_w_ratio"], "0.9")    # the look wins
        self.assertEqual(p["bottom_gradient"], "low")     # the title's still applies

    def test_bad_input_refused(self):
        for style in ({"tmdb_id": "1"}, {"access_key": "x"}, {"art_poster": "/x.jpg"}, {"Bad Name": "1"}):
            with self.assertRaises(ValueError):
                rules.clean_style(style)
        with self.assertRaises(ValueError):
            rules.clean_colors({"tint": "red"})
        with self.assertRaises(ValueError):
            rules.clean_crop({"x": 2})
        with self.assertRaises(ValueError):
            rules.set_mode(KEY, "pinned", 999)

    def test_never_lists_become_excludes(self):
        rules.set_never(KEY, "poster", "/bad.jpg", True)
        rules.set_never(KEY, "logo", "/bad.png", True)
        p = rules.resolve(KEY).params
        self.assertEqual(p["art_exclude"], "/bad.jpg")
        self.assertEqual(p["art_logo_exclude"], "/bad.png")
        rules.set_never(KEY, "poster", "/bad.jpg", False)
        self.assertNotIn("art_exclude", rules.resolve(KEY).params)

    def test_never_beats_a_pin(self):
        look = rules.add_look(KEY, {"poster": "/p.jpg"})
        rules.set_mode(KEY, "pinned", look["look_id"])
        rules.set_never(KEY, "poster", "/p.jpg", True)
        p = rules.resolve(KEY).params
        self.assertNotIn("art_poster", p)
        self.assertEqual(p["art_exclude"], "/p.jpg")

    def test_hands_off(self):
        rules.set_hands_off(KEY, True)
        self.assertTrue(rules.resolve(KEY).skip)
        self.assertFalse(rules.resolve(KEY, look_override={}).skip)   # the editor still previews it

    def test_deleting_the_pinned_look_goes_back_to_auto(self):
        look = rules.add_look(KEY, {"poster": "/p.jpg"})
        rules.set_mode(KEY, "pinned", look["look_id"])
        rules.delete_look(look["look_id"])
        self.assertEqual(rules.get_title(KEY)["mode"], "auto")

    def _rotation(self, n):
        ids = [rules.add_look(KEY, {"poster": f"/r{i}.jpg", "in_rotation": True})["look_id"] for i in range(n)]
        rules.set_mode(KEY, "rotation")
        return ids

    def test_rotation_covers_every_look_before_repeating(self):
        ids = self._rotation(4)
        rng, day, shown = random.Random(7), date(2026, 10, 1), []
        for i in range(12):
            shown.append(rules.resolve(KEY, today=day + timedelta(days=i), advance=True, rng=rng).look_id)
        for cycle in range(3):
            self.assertEqual(sorted(shown[cycle * 4:(cycle + 1) * 4]), sorted(ids))
        for a, b in zip(shown, shown[1:]):
            self.assertNotEqual(a, b)   # never the same look two days running, even across a reshuffle

    def test_rotation_only_moves_once_a_day_and_only_when_advancing(self):
        self._rotation(3)
        rng, day = random.Random(1), date(2026, 10, 1)
        first = rules.resolve(KEY, today=day, advance=True, rng=rng).look_id
        self.assertEqual(rules.resolve(KEY, today=day, advance=True, rng=rng).look_id, first)   # same day
        self.assertEqual(rules.resolve(KEY, today=day + timedelta(days=1), rng=rng).look_id, first)  # preview
        self.assertNotEqual(rules.resolve(KEY, today=day + timedelta(days=1), advance=True, rng=rng).look_id, first)

    def test_rotation_reshuffles_when_the_pool_changes(self):
        ids = self._rotation(3)
        rules.resolve(KEY, today=date(2026, 10, 1), advance=True, rng=random.Random(2))
        rules.set_never(KEY, "poster", "/r0.jpg", True)       # drops out of the pool
        res = rules.resolve(KEY, today=date(2026, 10, 2), advance=True, rng=random.Random(2))
        self.assertIn(res.look_id, ids[1:])
        self.assertEqual(sorted(rules.get_title(KEY)["deck"]), sorted(ids[1:]))

    def test_rotation_upcoming(self):
        self._rotation(3)
        res = rules.resolve(KEY, today=date(2026, 10, 1), advance=True, rng=random.Random(3))
        self.assertEqual(len(res.upcoming), 2)
        self.assertNotIn(res.look_id, res.upcoming)

    def test_summaries(self):
        self._rotation(2)
        rules.set_never(KEY, "logo", "/x.png", True)
        s = rules.summaries()[KEY]
        self.assertEqual((s["mode"], s["rotation"], s["never"]), ("rotation", 2, 1))


try:
    from studio import hooks
except Exception:   # the renderer's modules need the Linux image's libraries
    hooks = None


@unittest.skipIf(hooks is None, "renderer modules not importable here")
class HookTests(unittest.TestCase):
    def test_skip_excluded(self):
        data = {"poster_pools": {"textless": ["/a.jpg", "/b.jpg", "/c.jpg"]}, "original_poster_path": "/o.jpg"}
        self.assertEqual(hooks.skip_excluded(("/a.jpg",), "/a.jpg", True, "/bd.jpg", data), ("/b.jpg", True, "/bd.jpg"))
        self.assertEqual(hooks.skip_excluded(("/a.jpg", "/b.jpg", "/c.jpg"), "/a.jpg", True, "/bd.jpg", data),
                         ("/o.jpg", False, "/bd.jpg"))
        self.assertEqual(hooks.skip_excluded(("/bd.jpg",), "/a.jpg", True, "/bd.jpg", data), ("/a.jpg", True, None))

    def test_apply_params_validates(self):
        class Cfg:
            art_poster = art_crop = art_logo = ""
            art_original = False
            art_exclude = art_logo_exclude = ()
            tint_color = fade_color = logo_color = None
            logo_color_mode = "solid"
        import main
        cfg = Cfg()
        hooks.apply_params(cfg, {"art_poster": "http://evil.example/x.jpg", "art_logo": "text",
                                 "art_exclude": "/a.jpg,http://evil.example/y.jpg", "tint_color": "#ff0000"},
                           main._parse_hex_color)
        self.assertEqual(cfg.art_poster, "")           # not an art provider: ignored
        self.assertEqual(cfg.art_logo, "text")
        self.assertEqual(cfg.art_exclude, ("/a.jpg",))
        self.assertEqual(cfg.tint_color, (255, 0, 0))

    def test_recolor_logo(self):
        from PIL import Image
        logo = Image.new("RGBA", (4, 4), (10, 10, 10, 255))
        logo.putpixel((0, 0), (0, 0, 0, 0))
        out = hooks.recolor_logo(logo, (255, 255, 255))
        self.assertEqual(out.getpixel((1, 1)), (255, 255, 255, 255))
        self.assertEqual(out.getpixel((0, 0))[3], 0)

    def test_render_config_accepts_studio_params(self):
        import main
        cfg = main.build_request_config({"art_poster": "/abc.jpg", "art_crop": "0.5,0.5,2", "fade_color": "102030"})
        self.assertEqual((cfg.art_poster, cfg.art_crop, cfg.fade_color), ("/abc.jpg", "0.5000,0.5000,2.000", (16, 32, 48)))
        # Defaults leave the composite cache signature exactly as before.
        self.assertNotIn("art_poster", main._render_config_signature(main.build_request_config({})))


if __name__ == "__main__":
    unittest.main()
