import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from web import create_app


class SchedulePageTests(unittest.TestCase):
    def test_schedule_page_renders_operations_layout(self):
        app = create_app(scheduler=SimpleNamespace(get_jobs=lambda: []))

        async def fake_query(sql, params):
            if "FROM run_log" in sql:
                return [
                    {
                        "task_type": "content_planning",
                        "last_success": "2026-04-18 07:00",
                        "last_failure": None,
                    }
                ]
            if "FROM content_queue" in sql:
                return [
                    {
                        "id": 42,
                        "scheduled_date": "2026-04-19",
                        "scheduled_time": "09:30",
                        "content_type": "post",
                        "topic": "Weekend tasting menu",
                        "status": "approved",
                        "image_url": "https://example.com/post.jpg",
                    }
                ]
            raise AssertionError(f"Unexpected query: {sql}")

        async def fake_stats(_brand_id):
            return {
                "followers": 1200,
                "pending_count": 3,
                "approved_count": 7,
                "last_run_short": "04-18 09:00",
            }

        with (
            patch("web.routes.schedule.query", fake_query),
            patch("web.routes.schedule.get_dashboard_brand", lambda request: 1),
            patch("web.routes.schedule.get_brand_context", lambda request: {}),
            patch("web.routes.dashboard._global_stats", fake_stats),
        ):
            client = TestClient(app)
            response = client.get("/schedule")

        self.assertEqual(response.status_code, 200)
        self.assertIn("schedule-hero", response.text)
        self.assertIn("schedule-board", response.text)
        self.assertIn("schedule-upcoming-rail", response.text)
        self.assertIn("schedule-jobs-table", response.text)
        self.assertIn("<th scope=\"col\">Job</th>", response.text)
        self.assertIn("<th scope=\"col\">Schedule</th>", response.text)
        self.assertIn("<th scope=\"col\">Status</th>", response.text)
        self.assertIn("<th scope=\"col\">Next run</th>", response.text)
        self.assertIn("<th scope=\"col\">Last success</th>", response.text)
        self.assertIn("<th scope=\"col\">Last failure</th>", response.text)
        self.assertIn("Culinary Review", response.text)
