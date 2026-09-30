"""The Style page's API: drafts, apply, discard, undo, import."""
import os
import tempfile
import unittest
from urllib.parse import parse_qsl

from fastapi import FastAPI
from fastapi.testclient import TestClient

from studio import api_style, auth, db, prefs


class StyleApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.connect(os.path.join(self.tmp.name, "studio.db"))
        # The renderer-backed helpers are exercised by the full suite; here they're stubbed.
        self._orig = (api_style._defaults, api_style._sash_info)
        api_style._defaults = lambda: {"top_gradient": "high"}
        api_style._sash_info = lambda style: {"active": [], "all": []}
        app = FastAPI()
        app.include_router(api_style.router)
        app.dependency_overrides[auth.require] = lambda: None
        self.c = TestClient(app)

    def tearDown(self):
        api_style._defaults, api_style._sash_info = self._orig
        db._conn.close()
        db._conn = None
        self.tmp.cleanup()

    def applied(self):
        return dict(parse_qsl(prefs.get("style_applied")))

    def test_starts_with_the_imported_style_and_no_draft(self):
        d = self.c.get("/studio/api/style").json()
        self.assertFalse(d["has_draft"])
        self.assertEqual(d["params"]["sash_mode"], "notch")

    def test_edit_makes_a_draft_and_leaves_applied_alone(self):
        params = {**self.applied(), "top_gradient": "low"}
        d = self.c.put("/studio/api/style", json={"params": params}).json()
        self.assertTrue(d["has_draft"])
        self.assertEqual(d["params"]["top_gradient"], "low")
        self.assertEqual(self.applied()["top_gradient"], "off")   # Jellyfin's style unchanged

    def test_saving_the_applied_settings_drops_the_draft(self):
        self.c.put("/studio/api/style", json={"params": {**self.applied(), "top_gradient": "low"}})
        d = self.c.put("/studio/api/style", json={"params": self.applied()}).json()
        self.assertFalse(d["has_draft"])

    def test_discard(self):
        self.c.put("/studio/api/style", json={"params": {**self.applied(), "top_gradient": "low"}})
        self.assertFalse(self.c.post("/studio/api/style/discard").json()["has_draft"])
        self.assertEqual(self.applied()["top_gradient"], "off")

    def test_apply_then_undo(self):
        before = prefs.get("style_applied")
        self.c.put("/studio/api/style", json={"params": {**self.applied(), "top_gradient": "low"}})
        d = self.c.post("/studio/api/style/apply", json={"run": False}).json()
        self.assertFalse(d["has_draft"])
        self.assertEqual(self.applied()["top_gradient"], "low")
        self.assertFalse(d["run_started"])
        d = self.c.post("/studio/api/style/undo").json()
        self.assertTrue(d["has_draft"])
        self.assertEqual(d["draft"], before)

    def test_apply_without_draft_is_refused(self):
        self.assertEqual(self.c.post("/studio/api/style/apply", json={}).status_code, 400)

    def test_import_strips_ids_and_keys(self):
        url = "http://x:8183/poster?tmdb_id=1&type=movie&access_key=s3cret&sash_mode=sash&top_gradient=medium"
        d = self.c.post("/studio/api/style/import", json={"url": url}).json()
        self.assertEqual(d["params"], {"sash_mode": "sash", "top_gradient": "medium"})
        self.assertNotIn("s3cret", d["draft"])

    def test_identity_params_refused(self):
        r = self.c.put("/studio/api/style", json={"params": {"tmdb_id": "1"}})
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()
