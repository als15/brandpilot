import json
import os
import base64
import requests
import fal_client
from langchain_core.tools import tool
from db.connection import get_db

IMAGES_PER_MODEL = 2

MODELS = {
    "flux-2-pro": {
        "endpoint": "fal-ai/flux-2-pro",
        "args": {
            "image_size": {"width": 2048, "height": 2048},
            "num_images": IMAGES_PER_MODEL,
            "safety_tolerance": 5,
        },
    },
    "nano-banana-2": {
        "endpoint": "fal-ai/nano-banana-2",
        "args": {
            "resolution": "2K",
            "aspect_ratio": "1:1",
            "num_images": IMAGES_PER_MODEL,
        },
    },
}


def _generate_images(prompt: str, model_key: str) -> list[str]:
    """Call fal.ai to generate images with the given model. Returns list of image URLs."""
    os.environ["FAL_KEY"] = os.environ.get("FAL_KEY", "")

    model = MODELS[model_key]
    all_urls = []
    # Some models (flux-2-pro) ignore num_images, so we call multiple times if needed
    num_requested = model["args"].get("num_images", 1)
    args = {**model["args"], "num_images": 1}

    for _ in range(num_requested):
        result = fal_client.subscribe(
            model["endpoint"],
            arguments={"prompt": prompt, **args},
        )
        all_urls.extend(img["url"] for img in result["images"])

    return all_urls


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


def _generate_upscale_and_host(prompt: str, model_key: str) -> list[str]:
    """Generate images, upscale each, and rehost. Returns list of permanent URLs."""
    fal_urls = _generate_images(prompt, model_key)
    hosted = []
    for fal_url in fal_urls:
        upscaled_url = _upscale_image(fal_url)
        hosted.append(_rehost_image(upscaled_url))
    return hosted


@tool
def generate_and_host_image(prompt: str, post_id: int) -> str:
    """Generate images using two AI models, upscale them, and upload for permanent hosting.
    Generates multiple candidates per model for comparison via Telegram.
    Args:
        prompt: Detailed description of the image to generate. Be specific about composition, style, lighting.
        post_id: The content queue item ID to update with the generated image.
    """
    try:
        # Generate, upscale, and host images for each model
        candidates = {}
        for model_key in MODELS:
            candidates[model_key] = _generate_upscale_and_host(prompt, model_key)

        # Store first of each model as primary/alt, full list as JSON for Telegram picker
        all_candidates = json.dumps(candidates)

        db = get_db()
        db.execute(
            "UPDATE content_queue SET image_url = ?, image_url_alt = ?, "
            "image_candidates = ?, status = 'pending_approval' WHERE id = ?",
            (candidates["flux-2-pro"][0], candidates["nano-banana-2"][0],
             all_candidates, post_id),
        )
        db.commit()

        summary_lines = [f"Images generated and upscaled for post {post_id}."]
        for model_key, urls in candidates.items():
            for i, url in enumerate(urls, 1):
                summary_lines.append(f"  {model_key} #{i}: {url}")
        return "\n".join(summary_lines)

    except Exception as e:
        return f"Failed to generate image for post {post_id}: {e}"
