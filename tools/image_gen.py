import json
import logging
import os
import base64
import requests
import fal_client
from langchain_core.tools import tool
from db.connection import get_db

log = logging.getLogger(__name__)

MODELS = {
    "flux-2-pro": {
        "endpoint": "fal-ai/flux-2-pro",
        "args": {
            "image_size": {"width": 2048, "height": 2048},
            "safety_tolerance": 5,
        },
    },
    "nano-banana-2": {
        "endpoint": "fal-ai/nano-banana-2",
        "args": {
            "resolution": "2K",
            "aspect_ratio": "1:1",
        },
    },
}

MODEL_LABELS = {"flux-2-pro": "flux", "nano-banana-2": "banana"}


def _generate_image(prompt: str, model_key: str) -> str:
    """Call fal.ai to generate one image. Returns the temporary image URL."""
    os.environ["FAL_KEY"] = os.environ.get("FAL_KEY", "")

    model = MODELS[model_key]
    result = fal_client.subscribe(
        model["endpoint"],
        arguments={"prompt": prompt, "num_images": 1, **model["args"]},
    )
    return result["images"][0]["url"]


def _upscale_image(image_url: str) -> str:
    """Upscale an image using Real-ESRGAN via fal.ai. Returns the upscaled image URL."""
    result = fal_client.subscribe(
        "fal-ai/esrgan",
        arguments={
            "image_url": image_url,
            "model": "RealESRGAN_x2plus",
        },
    )
    return result["image"]["url"]


def _upload_to_imgbb(image_bytes: bytes) -> str:
    """Upload image bytes to imgbb and return the public URL."""
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    resp = requests.post(
        "https://api.imgbb.com/1/upload",
        data={
            "key": os.environ["IMGBB_API_KEY"],
            "image": b64,
        },
    )
    resp.raise_for_status()
    return resp.json()["data"]["url"]


def _rehost_image(fal_url: str) -> str:
    """Download image from fal.ai temp URL and rehost on imgbb for permanent public URL."""
    resp = requests.get(fal_url)
    resp.raise_for_status()
    return _upload_to_imgbb(resp.content)


def upscale_and_host(image_url: str) -> str:
    """Upscale an image and rehost it. Returns the permanent public URL."""
    upscaled_url = _upscale_image(image_url)
    return _rehost_image(upscaled_url)


def generate_one(prompt: str, model_key: str) -> str:
    """Generate one image, rehost it (no upscale). Returns permanent URL."""
    fal_url = _generate_image(prompt, model_key)
    return _rehost_image(fal_url)


@tool
def generate_and_host_image(prompt: str, post_id: int) -> str:
    """Generate one preview image per model (2 total) for comparison via Telegram.
    Upscaling happens only after the user picks their favorite.
    Args:
        prompt: Detailed description of the image to generate.
        post_id: The content queue item ID to update with the generated images.
    """
    try:
        previews = {}
        for model_key in MODELS:
            previews[model_key] = generate_one(prompt, model_key)

        db = get_db()
        db.execute(
            "UPDATE content_queue SET image_url = ?, image_url_alt = ?, "
            "status = 'pending_approval' WHERE id = ?",
            (previews["flux-2-pro"], previews["nano-banana-2"], post_id),
        )
        db.commit()

        return (
            f"Preview images generated for post {post_id}.\n"
            f"  A (flux-2-pro): {previews['flux-2-pro']}\n"
            f"  B (nano-banana-2): {previews['nano-banana-2']}\n"
            f"User will pick via Telegram, then the winner gets upscaled."
        )
    except Exception as e:
        return f"Failed to generate image for post {post_id}: {e}"
