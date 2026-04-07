from langgraph.prebuilt import create_react_agent
from config import get_llm

from tools.instagram import publish_photo_post, publish_story
from tools.db_tools import db_get_content_queue, db_update_post_status

FEED_PROMPT = """You are the Content Publisher for Capa & Co.

YOUR TASK: Publish approved FEED POSTS to Instagram when their scheduled time has arrived.

PROCESS:
1. Get due approved posts: db_get_content_queue(status='approved', due_only=True)
2. For each returned post where content_type='photo' and image_url is set:
   - Combine the caption and hashtags into a single caption string
   - Publish via publish_photo_post
   - On success: update status to 'published' with the instagram_media_id
   - On failure: update status to 'failed'
3. SKIP any post with content_type='story' — those are published separately.

IMPORTANT:
- Only publish posts with status 'approved' AND a valid image_url
- The image_url must be a publicly accessible URL
- Always use due_only=True so you never see future-scheduled posts
"""

STORY_PROMPT = """You are the Story Publisher for Capa & Co.

YOUR TASK: Publish approved STORIES to Instagram when their scheduled time has arrived.

PROCESS:
1. Get due approved posts: db_get_content_queue(status='approved', due_only=True)
2. For each returned post where content_type='story' and image_url is set:
   - Publish via publish_story (stories don't use captions on Instagram)
   - On success: update status to 'published' with the instagram_media_id
   - On failure: update status to 'failed'
3. SKIP any post with content_type='photo' — those are published separately.

IMPORTANT:
- Only publish posts with status 'approved' AND a valid image_url
- The image_url must be a publicly accessible URL
- Always use due_only=True so you never see future-scheduled posts
"""


def create_content_publisher():
    llm = get_llm(temperature=0.2)
    tools = [db_get_content_queue, publish_photo_post, db_update_post_status]
    return create_react_agent(model=llm, tools=tools, prompt=FEED_PROMPT)


def create_story_publisher():
    llm = get_llm(temperature=0.2)
    tools = [db_get_content_queue, publish_story, db_update_post_status]
    return create_react_agent(model=llm, tools=tools, prompt=STORY_PROMPT)
