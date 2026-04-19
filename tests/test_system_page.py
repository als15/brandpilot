import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from web import create_app


class SystemPageTests(unittest.TestCase):
    def test_system_page_renders_control_room_layout(self):
        app = create_app(scheduler=SimpleNamespace(get_jobs=lambda: []))

        async def fake_query_one(sql, params):
            if "SELECT " in sql and "content_count" in sql:
                return {
                    "content_count": 12,
                    "leads_count": 4,
                    "engagement_count": 8,
                    "run_count": 32,
                    "snapshot_count": 9,
                    "perf_count": 15,
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
            patch("web.routes.system.query_one", fake_query_one),
            patch("web.routes.system.get_dashboard_brand", lambda request: 1),
            patch("web.routes.system.get_brand_context", lambda request: {}),
            patch("web.routes.dashboard._global_stats", fake_stats),
            patch("pathlib.Path.exists", lambda self: False),
        ):
            client = TestClient(app)
            response = client.get("/system")

        self.assertEqual(response.status_code, 200)
        self.assertIn("system-hero", response.text)
        self.assertIn("system-control-grid", response.text)
        self.assertIn("system-log-panel", response.text)
