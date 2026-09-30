"""Studio's library of your own images per title."""
import os
import tempfile
import unittest

from studio import db, rules, uploads

K = "tmdb:movie:1"


class UploadsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.connect(os.path.join(self.tmp.name, "studio.db"))

    def tearDown(self):
        db._conn.close()
        db._conn = None
        self.tmp.cleanup()

    def test_several_uploads_are_all_kept(self):
        uploads.add(K, "poster", "custom:aaaaaaaaaaaaaaaa.jpg", "first.jpg")
        uploads.add(K, "poster", "custom:bbbbbbbbbbbbbbbb.jpg", "second.jpg")
        uploads.add(K, "backdrop", "custom:cccccccccccccccc.jpg")
        uploads.add(K, "logo", "custom:dddddddddddddddd.png")
        mine = uploads.for_title(K)
        self.assertEqual(sorted((u["kind"], u["path"][7:8]) for u in mine),
                         [("backdrop", "c"), ("logo", "d"), ("poster", "a"), ("poster", "b")])
        # Pinning one doesn't make the other disappear.
        look = rules.add_look(K, {"poster": "custom:bbbbbbbbbbbbbbbb.jpg"})
        rules.set_mode(K, "pinned", look["look_id"])
        self.assertEqual(len(uploads.for_title(K)), 4)

    def test_bad_kind(self):
        with self.assertRaises(ValueError):
            uploads.add(K, "sticker", "custom:aaaaaaaaaaaaaaaa.jpg")

    def test_own_title_follows_into_looks(self):
        uploads.add(K, "poster", "custom:aaaaaaaaaaaaaaaa.jpg")
        look = rules.add_look(K, {"poster": "custom:aaaaaaaaaaaaaaaa.jpg"})
        uploads.set_own_title(K, "custom:aaaaaaaaaaaaaaaa.jpg", True)
        self.assertTrue(uploads.get(K, "custom:aaaaaaaaaaaaaaaa.jpg")["own_title"])
        self.assertTrue(rules.get_look(look["look_id"])["own_title"])

    def test_images_used_by_looks_before_the_table_still_show(self):
        rules.add_look(K, {"poster": "custom:eeeeeeeeeeeeeeee.jpg", "logo": "custom:ffffffffffffffff.png"})
        kinds = {u["path"]: u["kind"] for u in uploads.for_title(K)}
        self.assertEqual(kinds, {"custom:eeeeeeeeeeeeeeee.jpg": "poster", "custom:ffffffffffffffff.png": "logo"})

    def test_remove_keeps_a_file_something_else_uses(self):
        uploads.add(K, "poster", "custom:aaaaaaaaaaaaaaaa.jpg")
        rules.add_look("tmdb:movie:2", {"poster": "custom:aaaaaaaaaaaaaaaa.jpg"})
        self.assertFalse(uploads.remove(K, "custom:aaaaaaaaaaaaaaaa.jpg"))   # file kept: another title uses it
        self.assertIsNone(uploads.get(K, "custom:aaaaaaaaaaaaaaaa.jpg"))
        self.assertTrue(uploads.in_use("custom:aaaaaaaaaaaaaaaa.jpg"))

    def test_studio_paths_for_the_artwork_clean_up(self):
        uploads.add(K, "logo", "custom:dddddddddddddddd.png")
        rules.add_look(K, {"poster": "custom:eeeeeeeeeeeeeeee.jpg", "logo": "/tmdb.png"})
        self.assertEqual(uploads.studio_custom_paths(),
                         {"custom:dddddddddddddddd.png", "custom:eeeeeeeeeeeeeeee.jpg"})


if __name__ == "__main__":
    unittest.main()
