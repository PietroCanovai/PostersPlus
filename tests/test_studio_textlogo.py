"""Text logos: a transparent PNG of the text, wrapped when long."""
import io
import unittest

from PIL import Image

try:
    from studio import textlogo
except Exception:   # the renderer's font module needs the Linux image's libraries
    textlogo = None


@unittest.skipIf(textlogo is None, "needs the renderer's fonts module")
class TextLogoTests(unittest.TestCase):
    def test_transparent_and_tight(self):
        im = Image.open(io.BytesIO(textlogo.render("Heat", font="bebas")))
        self.assertEqual(im.mode, "RGBA")
        self.assertEqual(im.getpixel((0, 0))[3], 0)                    # transparent corner
        self.assertGreater(im.width, im.height)

    def test_long_titles_wrap(self):
        one = Image.open(io.BytesIO(textlogo.render("Heat")))
        two = Image.open(io.BytesIO(textlogo.render("The Good, the Bad and the Ugly")))
        self.assertGreater(two.height, one.height * 1.5)                # two lines
        huge = Image.open(io.BytesIO(textlogo.render("The Assassination of Jesse James by the Coward Robert Ford")))
        self.assertLessEqual(huge.width, textlogo.MAX_W + 400)          # shrunk to fit, not overflowing

    def test_options(self):
        o = textlogo.options({"font": "playfair", "upper": "true", "shadow": "false", "spacing": "9"})
        self.assertEqual((o["font"], o["upper"], o["shadow"], o["spacing"]), ("playfair", True, False, 0.5))
        with self.assertRaises(ValueError):
            textlogo.render("   ")
        self.assertTrue(textlogo.available())


if __name__ == "__main__":
    unittest.main()
