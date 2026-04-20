"""Tests for brand-scoped Meta token persistence and refresh.

Uses a temp SQLite file as the backing DB so we exercise the same code path
as production minus the Postgres vs SQLite dialect switch. That switch is
covered by the existing _add_column_if_missing / set_credential branches,
not re-tested here.
"""

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch


class TokenRefreshTests(unittest.TestCase):
    def setUp(self):
        # Point the connection module at a fresh temp SQLite file and drop any
        # cached thread-local connection so each test starts clean.
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self._prev_db_url = os.environ.pop("DATABASE_URL", None)
        self._prev_db_path = os.environ.get("DATABASE_PATH")
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
        if self._prev_db_url is not None:
            os.environ["DATABASE_URL"] = self._prev_db_url
        if self._prev_db_path is not None:
            os.environ["DATABASE_PATH"] = self._prev_db_path
        else:
            os.environ.pop("DATABASE_PATH", None)
        self._schema_mod._init_done = False

    def test_credential_roundtrip(self):
        from tools.brand_credentials import get_credential, set_credential

        expires = datetime.now(timezone.utc) + timedelta(days=42)
        set_credential("mila", "META_ACCESS_TOKEN", "tok-abc", expires_at=expires)

        cred = get_credential("mila", "META_ACCESS_TOKEN")
        self.assertIsNotNone(cred)
        self.assertEqual(cred.value, "tok-abc")
        self.assertIsNotNone(cred.expires_at)
        self.assertEqual(get_credential("capa-co", "META_ACCESS_TOKEN"), None)

    def test_upsert_overwrites_existing(self):
        from tools.brand_credentials import get_credential, set_credential

        set_credential("mila", "META_ACCESS_TOKEN", "old")
        set_credential("mila", "META_ACCESS_TOKEN", "new")
        self.assertEqual(get_credential("mila", "META_ACCESS_TOKEN").value, "new")

    def test_expires_in_days_negative_when_expired(self):
        from tools.brand_credentials import credential_expires_in_days, set_credential

        past = datetime.now(timezone.utc) - timedelta(days=3)
        set_credential("mila", "META_ACCESS_TOKEN", "t", expires_at=past)
        self.assertLess(credential_expires_in_days("mila", "META_ACCESS_TOKEN"), 0)

    def test_expires_in_days_none_when_no_expiry_stored(self):
        from tools.brand_credentials import credential_expires_in_days, set_credential

        set_credential("mila", "META_ACCESS_TOKEN", "t", expires_at=None)
        self.assertIsNone(credential_expires_in_days("mila", "META_ACCESS_TOKEN"))

    def test_load_persisted_token_prefers_db_over_env(self):
        from tools.brand_credentials import set_credential
        from tools.token_refresh import load_persisted_token

        set_credential("mila", "META_ACCESS_TOKEN", "from-db")
        os.environ["META_ACCESS_TOKEN"] = "from-env"
        try:
            loaded = load_persisted_token("mila")
            self.assertTrue(loaded)
            self.assertEqual(os.environ["META_ACCESS_TOKEN"], "from-db")
        finally:
            os.environ.pop("META_ACCESS_TOKEN", None)

    def test_load_persisted_token_bootstrap_leaves_env_alone(self):
        from tools.token_refresh import load_persisted_token

        os.environ["META_ACCESS_TOKEN"] = "bootstrap"
        try:
            loaded = load_persisted_token("mila")
            self.assertFalse(loaded)
            self.assertEqual(os.environ["META_ACCESS_TOKEN"], "bootstrap")
        finally:
            os.environ.pop("META_ACCESS_TOKEN", None)

    def test_refresh_writes_new_token_and_expiry_to_db(self):
        from tools.brand_credentials import get_credential, set_credential
        from tools.token_refresh import refresh_meta_token

        set_credential("mila", "META_ACCESS_TOKEN", "old-token")

        class FakeResp:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {"access_token": "new-token", "expires_in": 60 * 24 * 3600}

        with patch("tools.token_refresh.requests.get", return_value=FakeResp()):
            returned = refresh_meta_token("mila")

        self.assertEqual(returned, "new-token")
        cred = get_credential("mila", "META_ACCESS_TOKEN")
        self.assertEqual(cred.value, "new-token")
        self.assertIsNotNone(cred.expires_at)
        days = (cred.expires_at - datetime.now(timezone.utc)).days
        self.assertGreaterEqual(days, 58)
        self.assertLessEqual(days, 60)
        self.assertEqual(os.environ.get("META_ACCESS_TOKEN"), "new-token")
        os.environ.pop("META_ACCESS_TOKEN", None)

    def test_refresh_raises_when_no_bootstrap_token(self):
        from tools.token_refresh import refresh_meta_token

        os.environ.pop("META_ACCESS_TOKEN", None)
        with self.assertRaises(RuntimeError):
            refresh_meta_token("mila")

    def test_refresh_falls_back_to_default_ttl_when_api_omits_expires_in(self):
        from tools.brand_credentials import get_credential
        from tools.token_refresh import refresh_meta_token

        os.environ["META_ACCESS_TOKEN"] = "bootstrap-token"

        class FakeResp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"access_token": "new-token"}

        try:
            with patch("tools.token_refresh.requests.get", return_value=FakeResp()):
                refresh_meta_token("mila")
            cred = get_credential("mila", "META_ACCESS_TOKEN")
            self.assertIsNotNone(cred.expires_at)
            days = (cred.expires_at - datetime.now(timezone.utc)).days
            self.assertGreaterEqual(days, 58)
        finally:
            os.environ.pop("META_ACCESS_TOKEN", None)


if __name__ == "__main__":
    unittest.main()
