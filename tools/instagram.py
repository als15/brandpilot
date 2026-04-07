import logging
import os
import time
import requests
from datetime import date
from langchain_core.tools import tool

log = logging.getLogger("capaco")

from db.connection import get_db

GRAPH_API_BASE = "https://graph.facebook.com/v21.0"

# Daily publish limits
MAX_POSTS_PER_DAY = 1
MAX_STORIES_PER_DAY = 2


def _wait_for_container(container_id: str, max_wait: int = 60, interval: int = 5):
    """Poll container status until FINISHED or timeout."""
    url = f"{GRAPH_API_BASE}/{container_id}"
    params = {"fields": "status_code"}
    for _ in range(max_wait // interval):
        resp = requests.get(url, params=params, headers=_get_headers(), timeout=30)
        if resp.status_code == 200:
            status = resp.json().get("status_code")
            if status == "FINISHED":
                return
            if status == "ERROR":
                raise RuntimeError(f"Instagram container {container_id} failed processing")
        log.info(f"Container {container_id} not ready (status={resp.json().get('status_code', '?')}), waiting {interval}s...")
        time.sleep(interval)
    raise RuntimeError(f"Container {container_id} not ready after {max_wait}s timeout")


def _published_today(content_type: str) -> int:
    """Count how many posts/stories were published today."""
    db = get_db()
    today = date.today().isoformat()
    row = db.execute(
        "SELECT COUNT(*) as cnt FROM content_queue "
        "WHERE status = 'published' AND content_type = ? "
        "AND date(published_at) = ?",
        (content_type, today),
    ).fetchone()
    return (row["cnt"] if row else 0) or 0


def _get_headers():
    return {"Authorization": f"Bearer {os.environ['META_ACCESS_TOKEN']}"}


def _ig_account_id():
    return os.environ["INSTAGRAM_ACCOUNT_ID"]


@tool
def get_instagram_profile() -> dict:
    """Get the Instagram Business account profile info including follower count, media count, and bio."""
    url = f"{GRAPH_API_BASE}/{_ig_account_id()}"
    params = {"fields": "username,name,biography,followers_count,follows_count,media_count"}
    resp = requests.get(url, params=params, headers=_get_headers(), timeout=30)
    resp.raise_for_status()
    return resp.json()


@tool
def get_recent_media(limit: int = 10) -> list[dict]:
    """Get recent Instagram posts with engagement metrics. Use this to understand what content performs well."""
    url = f"{GRAPH_API_BASE}/{_ig_account_id()}/media"
    params = {
        "fields": "id,caption,timestamp,like_count,comments_count,media_type,permalink",
        "limit": limit,
    }
    resp = requests.get(url, params=params, headers=_get_headers(), timeout=30)
    resp.raise_for_status()
    return resp.json().get("data", [])


@tool
def get_media_insights(media_id: str) -> dict:
    """Get detailed insights for a specific post (impressions, reach, engagement).
    Args:
        media_id: The Instagram media ID to get insights for.
    """
    url = f"{GRAPH_API_BASE}/{media_id}/insights"
    params = {"metric": "impressions,reach,engagement,saved"}
    resp = requests.get(url, params=params, headers=_get_headers(), timeout=30)
    resp.raise_for_status()
    return resp.json().get("data", [])


@tool
def get_account_insights(period: str = "day", days: int = 7) -> dict:
    """Get account-level insights like impressions, reach, and follower count over time.
    Args:
        period: The aggregation period - 'day' or 'week'.
        days: Number of days to look back (max 30).
    """
    from datetime import datetime, timedelta

    until = datetime.now()
    since = until - timedelta(days=days)

    url = f"{GRAPH_API_BASE}/{_ig_account_id()}/insights"
    params = {
        "metric": "impressions,reach,follower_count",
        "period": period,
        "since": int(since.timestamp()),
        "until": int(until.timestamp()),
    }
    resp = requests.get(url, params=params, headers=_get_headers(), timeout=30)
    resp.raise_for_status()
    return resp.json().get("data", [])


@tool
def publish_photo_post(image_url: str, caption: str) -> dict:
    """Publish a photo post to Instagram. The image must be a publicly accessible URL.
    Args:
        image_url: A publicly accessible URL of the image to post.
        caption: The post caption including hashtags.
    """
    count = _published_today("photo")
    if count >= MAX_POSTS_PER_DAY:
        raise RuntimeError(
            f"Daily post limit reached: {count}/{MAX_POSTS_PER_DAY} photo posts already published today. "
            "Cannot publish more until tomorrow."
        )

    # Step 1: Create media container
    url = f"{GRAPH_API_BASE}/{_ig_account_id()}/media"
    payload = {"image_url": image_url, "caption": caption}
    resp = requests.post(url, data=payload, headers=_get_headers(), timeout=30)
    if resp.status_code != 200:
        error_detail = resp.text[:500]
        raise RuntimeError(f"Instagram container creation failed ({resp.status_code}): {error_detail}")
    container_id = resp.json()["id"]

    # Step 2: Wait for container to be ready
    _wait_for_container(container_id)

    # Step 3: Publish the container
    publish_url = f"{GRAPH_API_BASE}/{_ig_account_id()}/media_publish"
    publish_resp = requests.post(
        publish_url, data={"creation_id": container_id}, headers=_get_headers(), timeout=30
    )
    if publish_resp.status_code != 200:
        error_detail = publish_resp.text[:500]
        raise RuntimeError(f"Instagram publish failed ({publish_resp.status_code}): {error_detail}")
    return publish_resp.json()


@tool
def publish_carousel_post(image_urls: list[str], caption: str) -> dict:
    """Publish a carousel post (multiple images) to Instagram.
    Args:
        image_urls: List of publicly accessible image URLs (2-10 images).
        caption: The post caption including hashtags.
    """
    # Step 1: Create individual media containers
    children_ids = []
    for img_url in image_urls:
        url = f"{GRAPH_API_BASE}/{_ig_account_id()}/media"
        payload = {"image_url": img_url, "is_carousel_item": True}
        resp = requests.post(url, data=payload, headers=_get_headers(), timeout=30)
        resp.raise_for_status()
        children_ids.append(resp.json()["id"])

    # Step 2: Create carousel container
    url = f"{GRAPH_API_BASE}/{_ig_account_id()}/media"
    payload = {
        "media_type": "CAROUSEL",
        "children": ",".join(children_ids),
        "caption": caption,
    }
    resp = requests.post(url, data=payload, headers=_get_headers(), timeout=30)
    resp.raise_for_status()
    container_id = resp.json()["id"]

    # Step 3: Wait for carousel container to be ready
    _wait_for_container(container_id)

    # Step 4: Publish
    publish_url = f"{GRAPH_API_BASE}/{_ig_account_id()}/media_publish"
    publish_resp = requests.post(
        publish_url, data={"creation_id": container_id}, headers=_get_headers(), timeout=30
    )
    publish_resp.raise_for_status()
    return publish_resp.json()


@tool
def publish_story(image_url: str) -> dict:
    """Publish an image as an Instagram Story.
    Args:
        image_url: A publicly accessible URL of the image to post as a story.
    """
    count = _published_today("story")
    if count >= MAX_STORIES_PER_DAY:
        raise RuntimeError(
            f"Daily story limit reached: {count}/{MAX_STORIES_PER_DAY} stories already published today. "
            "Cannot publish more until tomorrow."
        )

    # Step 1: Create story media container
    url = f"{GRAPH_API_BASE}/{_ig_account_id()}/media"
    payload = {"image_url": image_url, "media_type": "STORIES"}
    resp = requests.post(url, data=payload, headers=_get_headers(), timeout=30)
    if resp.status_code != 200:
        error_detail = resp.text[:500]
        raise RuntimeError(f"Instagram story container failed ({resp.status_code}): {error_detail}")
    container_id = resp.json()["id"]

    # Step 2: Wait for container to be ready
    _wait_for_container(container_id)

    # Step 3: Publish the container
    publish_url = f"{GRAPH_API_BASE}/{_ig_account_id()}/media_publish"
    publish_resp = requests.post(
        publish_url, data={"creation_id": container_id}, headers=_get_headers(), timeout=30
    )
    if publish_resp.status_code != 200:
        error_detail = publish_resp.text[:500]
        raise RuntimeError(f"Instagram story publish failed ({publish_resp.status_code}): {error_detail}")
    return publish_resp.json()


@tool
def exchange_for_long_lived_token() -> str:
    """Exchange the current short-lived token for a long-lived one (60 days). Run this once after getting a new token."""
    url = f"{GRAPH_API_BASE}/oauth/access_token"
    params = {
        "grant_type": "fb_exchange_token",
        "client_id": os.environ["META_APP_ID"],
        "client_secret": os.environ["META_APP_SECRET"],
        "fb_exchange_token": os.environ["META_ACCESS_TOKEN"],
    }
    resp = requests.get(url, params=params, timeout=30)
    if resp.status_code != 200:
        return f"Token exchange failed ({resp.status_code}): {resp.text}"
    data = resp.json()
    new_token = data["access_token"]
    # Auto-update the in-memory token and persist to .env
    os.environ["META_ACCESS_TOKEN"] = new_token
    _update_env_token(new_token)
    return f"Long-lived token saved to .env. Expires in {data.get('expires_in', 'unknown')} seconds"


def _update_env_token(new_token: str):
    """Update META_ACCESS_TOKEN in .env file."""
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path) as f:
        lines = f.readlines()
    with open(env_path, "w") as f:
        for line in lines:
            if line.startswith("META_ACCESS_TOKEN="):
                f.write(f"META_ACCESS_TOKEN={new_token}\n")
            else:
                f.write(line)
