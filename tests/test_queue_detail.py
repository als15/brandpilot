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

