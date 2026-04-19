import unittest
from datetime import datetime
from unittest.mock import patch

from fastapi.testclient import TestClient

from web import create_app


class LeadsPageTests(unittest.TestCase):
    def test_leads_page_renders_datetime_created_at(self):
        app = create_app(scheduler=None, bot=None, safe_run_fn=None)

        async def fake_query(sql, params):
            if "FROM leads" in sql and "ORDER BY created_at DESC" in sql:
                return [
                    {
                        "id": 1,
                        "created_at": datetime(2026, 4, 18, 9, 30),
                        "business_name": "Capa Test",
                        "instagram_handle": "capatest",
                        "business_type": "restaurant",
                        "location": "Tel Aviv",
                        "follower_count": 4200,
                        "source": "manual",
                        "status": "discovered",
                        "notes": "Test note",
                    }
                ]
            if "GROUP BY status" in sql:
                return [{"status": "discovered", "count": 1}]
            if "SELECT DISTINCT business_type" in sql:
                return [{"business_type": "restaurant"}]
            raise AssertionError(f"Unexpected query: {sql}")

        async def fake_stats(_brand_id):
            return {
                "followers": 1200,
                "pending_count": 3,
                "approved_count": 7,
                "last_run_short": "04-18 09:00",
            }

        with (
            patch("web.routes.leads.query", fake_query),
            patch("web.routes.leads.get_dashboard_brand", lambda request: "capa-co"),
            patch("web.routes.leads.get_brand_context", lambda request: {}),
            patch("web.routes.dashboard._global_stats", fake_stats),
        ):
            client = TestClient(app)
            response = client.get("/leads")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Added 2026-04-18", response.text)
