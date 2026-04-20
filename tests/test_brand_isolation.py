"""Tests for brand isolation in the scheduled-task path (issue #5).

These are regression tests for the bug where all agent modules did
``from brands.loader import brand_config``, capturing a stale reference
to the initial sentinel BrandConfig at module-import time. Result:
``publish_due_posts`` queried for content with the wrong brand_id, and
``mila`` publishing silently never ran.

The fix: every agent/helper on the scheduled-task path now takes a
``brand_slug`` parameter and reads ``brand_config`` dynamically via
``brands.loader.brand_config`` rather than the stale local import.
"""

import os
import tempfile
import unittest
from unittest.mock import patch


class BrandIsolationTests(unittest.TestCase):
    """publish_due_posts and daemon helpers must scope by brand_slug, not the global."""

    def setUp(self):
        # Snapshot os.environ so anything the test (or modules it imports)
        # adds — e.g. dotenv-loaded DASHBOARD_SECRET pulled in by daemon —
        # doesn't leak and affect other test classes.
        self._saved_env = dict(os.environ)
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        os.environ.pop("DATABASE_URL", None)
        os.environ["DATABASE_PATH"] = self._tmp.name

        from db import connection as conn_mod
        from db import schema as schema_mod

        conn_mod._local.__dict__.pop("connection", None)
        schema_mod._init_done = False
        schema_mod.init_db()
        self._conn_mod = conn_mod
        self._schema_mod = schema_mod

    def tearDown(self):
        self._conn_mod._local.__dict__.pop("connection", None)
        os.unlink(self._tmp.name)
        os.environ.clear()
        os.environ.update(self._saved_env)
        self._schema_mod._init_done = False

    def _insert_post(self, *, brand_id, post_id, content_type="photo",
                     scheduled_date="2026-04-19", scheduled_time="09:00"):
        db = self._conn_mod.get_db()
        db.execute(
            "INSERT INTO content_queue "
            "(id, brand_id, status, content_type, image_url, caption, hashtags, "
            "scheduled_date, scheduled_time) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (post_id, brand_id, "approved", content_type,
             "https://example.com/img.jpg", "c", "", scheduled_date, scheduled_time),
        )
        db.commit()

    def _load_mock_brand_config(self, slug):
        """Minimal stub so publish_due_posts can read timezone / content_strategy."""
        from brands.loader import BrandConfig
        bc = BrandConfig()
        bc.slug = slug
        bc.identity.timezone = "UTC"
        bc.content_strategy.max_posts_per_day = 10
        bc.content_strategy.max_stories_per_day = 10
        return bc

    def test_publish_due_posts_queries_by_passed_brand_slug_not_global(self):
        """If a mila row and a capa-co row both exist, publish_due_posts('photo', 'mila')
        must only consider the mila row — regardless of what the global brand_config
        holds."""
        from types import SimpleNamespace
        from agents import content_publisher

        self._insert_post(brand_id="mila", post_id=101)
        self._insert_post(brand_id="capa-co", post_id=102)

        captured = []

        def fake_photo_invoke(payload):
            captured.append(payload["image_url"])
            return {"id": "ig_media_xyz"}

        # Replace the LangChain tool object wholesale — easier than patching
        # pydantic-backed attributes.
        fake_photo_tool = SimpleNamespace(invoke=fake_photo_invoke)

        with (
            patch.object(content_publisher, "publish_photo_post", fake_photo_tool),
            patch.object(content_publisher, "_published_today", lambda _c: 0),
            patch.object(content_publisher, "BrandConfig") as MockBC,
        ):
            MockBC.load.side_effect = lambda slug: self._load_mock_brand_config(slug)
            summary = content_publisher.publish_due_posts("photo", "mila")

        self.assertIn("1 published", summary)
        self.assertEqual(len(captured), 1, "exactly one post should have been published")
        # Verify the mila post (id=101) was published, not capa-co's (id=102)
        db = self._conn_mod.get_db()
        mila_row = db.execute("SELECT status FROM content_queue WHERE id = 101").fetchone()
        capaco_row = db.execute("SELECT status FROM content_queue WHERE id = 102").fetchone()
        self.assertEqual(mila_row["status"], "published")
        self.assertEqual(capaco_row["status"], "approved")

    def test_has_publishable_content_scopes_to_brand_slug(self):
        """The daemon's _has_publishable_content must only see the passed brand's posts."""
        import daemon

        self._insert_post(brand_id="mila", post_id=201)

        with patch.object(daemon, "_brand_timezone", return_value=__import__("zoneinfo").ZoneInfo("UTC")):
            self.assertTrue(daemon._has_publishable_content("publish", "mila"))
            self.assertFalse(daemon._has_publishable_content("publish", "capa-co"))

    def test_skip_reason_scopes_to_brand_slug(self):
        """_skip_reason reports on the passed brand only."""
        import daemon

        self._insert_post(brand_id="capa-co", post_id=301,
                          scheduled_date="2026-04-19", scheduled_time="09:00")

        with patch.object(daemon, "_brand_timezone", return_value=__import__("zoneinfo").ZoneInfo("UTC")):
            reason = daemon._skip_reason("photo", "mila")

        # Since mila has no posts, the reason should mention that — not capa-co's state.
        self.assertIn("No", reason)
        self.assertNotIn("capa-co", reason)


if __name__ == "__main__":
    unittest.main()
