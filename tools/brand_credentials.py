"""Per-brand credential storage backed by the brand_credentials table.

Tokens must survive Railway redeploys; file-based persistence at data/meta_token.txt
got wiped every deploy and the 50-day auto-refresh was effectively defeated whenever
a deploy landed after the refresh (see GitHub issue #1).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from db.connection import _is_postgres, get_db

log = logging.getLogger("capaco")


@dataclass
class Credential:
    value: str
    expires_at: Optional[datetime]
    updated_at: Optional[datetime]


def _parse_ts(raw) -> Optional[datetime]:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(str(raw)).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def get_credential(brand_slug: str, key: str) -> Optional[Credential]:
    """Return the stored credential for (brand, key), or None if absent."""
    db = get_db()
    row = db.execute(
        "SELECT credential_value, expires_at, updated_at FROM brand_credentials "
        "WHERE brand_id = ? AND credential_key = ?",
        (brand_slug, key),
    ).fetchone()
    if not row:
        return None
    value = row["credential_value"]
    if value is None:
        return None
    return Credential(
        value=value,
        expires_at=_parse_ts(row["expires_at"]),
        updated_at=_parse_ts(row["updated_at"]),
    )


def set_credential(
    brand_slug: str,
    key: str,
    value: str,
    expires_at: Optional[datetime] = None,
) -> None:
    """Upsert a credential. Uses the DB's native UPSERT for atomicity."""
    db = get_db()
    now = datetime.now(timezone.utc)
    expires_iso = expires_at.isoformat() if expires_at else None
    if _is_postgres():
        db.execute(
            "INSERT INTO brand_credentials "
            "(brand_id, credential_key, credential_value, expires_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (brand_id, credential_key) DO UPDATE SET "
            "credential_value = EXCLUDED.credential_value, "
            "expires_at = EXCLUDED.expires_at, "
            "updated_at = EXCLUDED.updated_at",
            (brand_slug, key, value, expires_iso, now.isoformat()),
        )
    else:
        db.execute(
            "INSERT INTO brand_credentials "
            "(brand_id, credential_key, credential_value, expires_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (brand_id, credential_key) DO UPDATE SET "
            "credential_value = excluded.credential_value, "
            "expires_at = excluded.expires_at, "
            "updated_at = excluded.updated_at",
            (brand_slug, key, value, expires_iso, now.isoformat()),
        )
        db.commit()


def credential_expires_in_days(brand_slug: str, key: str) -> Optional[int]:
    """Days until the stored credential expires. None if unknown, negative if expired."""
    cred = get_credential(brand_slug, key)
    if not cred or not cred.expires_at:
        return None
    delta = cred.expires_at - datetime.now(timezone.utc)
    return int(delta.total_seconds() // 86400)
