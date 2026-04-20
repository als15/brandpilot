import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from web import create_app


class QueueDetailTests(unittest.TestCase):
    def test_poll_status_returns_regenerated_content_fields(self):
        app = create_app(scheduler=SimpleNamespace(get_jobs=lambda: []))

        async def fake_query_one(sql, params):
            self.assertIn("SELECT status, image_url, caption, topic, hashtags, visual_direction, content_pillar", sql)
            return {
                "status": "pending_approval",
                "image_url": "https://example.com/new-image.jpg",
                "caption": "חדש ומתאים לתמונה",
                "topic": "קרואסון פיסטוק",
                "hashtags": "#pistachio #croissant",
                "visual_direction": "Pistachio croissant on marble counter",
                "content_pillar": "product",
            }

        with (
            patch("web.routes.queue.query_one", fake_query_one),
            patch("web.routes.queue.get_dashboard_brand", lambda request: 1),
        ):
            client = TestClient(app)
            response = client.get("/queue/42/poll-status")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "status": "pending_approval",
                "image_url": "https://example.com/new-image.jpg",
                "caption": "חדש ומתאים לתמונה",
                "topic": "קרואסון פיסטוק",
                "hashtags": "#pistachio #croissant",
                "visual_direction": "Pistachio croissant on marble counter",
                "content_pillar": "product",
            },
        )

    def test_queue_detail_renders_live_update_targets_for_regenerated_content(self):
        app = create_app(scheduler=SimpleNamespace(get_jobs=lambda: []))

        async def fake_query_one(sql, params):
            if "SELECT * FROM content_queue" in sql:
                return {
                    "id": 42,
                    "brand_id": 1,
                    "status": "pending_approval",
                    "image_url": "https://example.com/original.jpg",
                    "caption": "ישן",
                    "topic": "נושא ישן",
                    "hashtags": "#old",
                    "visual_direction": "Old direction",
                    "content_pillar": "behind_the_scenes",
                    "content_type": "post",
                    "scheduled_date": "2026-04-19",
                    "scheduled_time": "09:30",
                    "notes": "",
                    "instagram_media_id": None,
                    "published_at": None,
                }
            raise AssertionError(f"Unexpected query: {sql}")

        async def fake_stats(_brand_id):
            return {
                "followers": 1200,
                "pending_count": 3,
                "approved_count": 7,
                "last_run_short": "04-18 09:00",
            }

        with (
            patch("web.routes.queue.query_one", fake_query_one),
            patch("web.routes.queue.get_dashboard_brand", lambda request: 1),
            patch("web.routes.queue.get_brand_context", lambda request: {}),
            patch("web.routes.dashboard._global_stats", fake_stats),
        ):
            client = TestClient(app)
            response = client.get("/queue/42")

        self.assertEqual(response.status_code, 200)
        self.assertIn('id="topic-display"', response.text)
        self.assertIn('id="visual-direction-display"', response.text)
        self.assertIn('id="content-pillar-display"', response.text)

    def test_queue_detail_shows_republish_button_for_failed_posts(self):
        app = create_app(scheduler=SimpleNamespace(get_jobs=lambda: []))

        async def fake_query_one(sql, params):
            if "SELECT * FROM content_queue" in sql:
                return {
                    "id": 56, "brand_id": 1, "status": "failed",
                    "image_url": "https://example.com/img.jpg", "caption": "c",
                    "topic": "t", "hashtags": "", "visual_direction": "",
                    "content_pillar": "product", "content_type": "post",
                    "scheduled_date": "2026-04-20", "scheduled_time": "09:00",
                    "notes": "", "instagram_media_id": None, "published_at": None,
                    "retry_count": 3,
                }
            raise AssertionError(f"Unexpected query: {sql}")

        async def fake_stats(_brand_id):
            return {"followers": 0, "pending_count": 0, "approved_count": 0, "last_run_short": ""}

        with (
            patch("web.routes.queue.query_one", fake_query_one),
            patch("web.routes.queue.get_dashboard_brand", lambda request: 1),
            patch("web.routes.queue.get_brand_context", lambda request: {}),
            patch("web.routes.dashboard._global_stats", fake_stats),
        ):
            client = TestClient(app)
            response = client.get("/queue/56")

        self.assertEqual(response.status_code, 200)
        self.assertIn("/queue/56/republish", response.text)
        self.assertIn("after 3 attempts", response.text)

    def test_republish_endpoint_resets_status_and_retry_count(self):
        app = create_app(scheduler=SimpleNamespace(get_jobs=lambda: []))
        captured = {}

        async def fake_execute(sql, params):
            captured["sql"] = sql
            captured["params"] = params

        with (
            patch("web.routes.queue.execute", fake_execute),
            patch("web.routes.queue.get_dashboard_brand", lambda request: "mila"),
        ):
            client = TestClient(app)
            response = client.post("/queue/56/republish")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Re-queued", response.text)
        self.assertIn("status = 'approved'", captured["sql"])
        self.assertIn("retry_count = 0", captured["sql"])
        self.assertIn("status = 'failed'", captured["sql"])
        self.assertEqual(captured["params"], (56, "mila"))

    def _render_detail(self, post):
        """Helper: render the queue detail page for a post dict and return the HTML."""
        app = create_app(scheduler=SimpleNamespace(get_jobs=lambda: []))

        async def fake_query_one(sql, params):
            if "SELECT * FROM content_queue" in sql:
                return post
            raise AssertionError(f"Unexpected query: {sql}")

        async def fake_stats(_brand_id):
            return {"followers": 0, "pending_count": 0, "approved_count": 0, "last_run_short": ""}

        with (
            patch("web.routes.queue.query_one", fake_query_one),
            patch("web.routes.queue.get_dashboard_brand", lambda request: 1),
            patch("web.routes.queue.get_brand_context", lambda request: {}),
            patch("web.routes.dashboard._global_stats", fake_stats),
        ):
            client = TestClient(app)
            response = client.get(f"/queue/{post['id']}")

        self.assertEqual(response.status_code, 200)
        return response.text

    def test_publish_now_button_renders_on_approved_posts_with_image(self):
        html = self._render_detail({
            "id": 77, "brand_id": 1, "status": "approved",
            "image_url": "https://example.com/img.jpg", "caption": "c",
            "topic": "t", "hashtags": "", "visual_direction": "",
            "content_pillar": "product", "content_type": "post",
            "scheduled_date": "2026-04-21", "scheduled_time": "09:00",
            "notes": "", "instagram_media_id": None, "published_at": None,
            "retry_count": 0,
        })
        self.assertIn("/queue/77/publish-now", html)
        self.assertIn("Publish Now", html)

    def test_publish_now_button_hidden_on_approved_posts_without_image(self):
        html = self._render_detail({
            "id": 78, "brand_id": 1, "status": "approved",
            "image_url": None, "caption": "c",
            "topic": "t", "hashtags": "", "visual_direction": "",
            "content_pillar": "product", "content_type": "post",
            "scheduled_date": "2026-04-21", "scheduled_time": "09:00",
            "notes": "", "instagram_media_id": None, "published_at": None,
            "retry_count": 0,
        })
        self.assertNotIn("/queue/78/publish-now", html)

    def test_publish_now_button_appears_alongside_republish_on_failed_posts(self):
        html = self._render_detail({
            "id": 56, "brand_id": 1, "status": "failed",
            "image_url": "https://example.com/img.jpg", "caption": "c",
            "topic": "t", "hashtags": "", "visual_direction": "",
            "content_pillar": "product", "content_type": "post",
            "scheduled_date": "2026-04-20", "scheduled_time": "09:00",
            "notes": "", "instagram_media_id": None, "published_at": None,
            "retry_count": 3,
        })
        self.assertIn("/queue/56/republish", html)
        self.assertIn("/queue/56/publish-now", html)

    def test_publish_now_endpoint_schedules_background_task(self):
        app = create_app(scheduler=SimpleNamespace(get_jobs=lambda: []))
        invoked = {}

        async def fake_query_one(sql, params):
            return {"status": "approved", "image_url": "https://example.com/img.jpg"}

        def fake_run_publish_now(post_id, brand_slug):
            invoked["post_id"] = post_id
            invoked["brand_slug"] = brand_slug

        with (
            patch("web.routes.queue.query_one", fake_query_one),
            patch("web.routes.queue.get_dashboard_brand", lambda request: "mila"),
            patch("web.routes.queue._run_publish_now", fake_run_publish_now),
        ):
            client = TestClient(app)
            response = client.post("/queue/77/publish-now")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Publishing", response.text)
        self.assertEqual(invoked, {"post_id": 77, "brand_slug": "mila"})

    def test_publish_now_endpoint_rejects_published_posts(self):
        app = create_app(scheduler=SimpleNamespace(get_jobs=lambda: []))

        async def fake_query_one(sql, params):
            return {"status": "published", "image_url": "https://example.com/img.jpg"}

        with (
            patch("web.routes.queue.query_one", fake_query_one),
            patch("web.routes.queue.get_dashboard_brand", lambda request: "mila"),
        ):
            client = TestClient(app)
            response = client.post("/queue/42/publish-now")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Not publishable", response.text)

    def test_publish_now_endpoint_rejects_missing_image(self):
        app = create_app(scheduler=SimpleNamespace(get_jobs=lambda: []))

        async def fake_query_one(sql, params):
            return {"status": "approved", "image_url": None}

        with (
            patch("web.routes.queue.query_one", fake_query_one),
            patch("web.routes.queue.get_dashboard_brand", lambda request: "mila"),
        ):
            client = TestClient(app)
            response = client.post("/queue/42/publish-now")

        self.assertEqual(response.status_code, 200)
        self.assertIn("No image", response.text)

