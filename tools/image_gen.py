import json
import logging
import os
import base64
import requests
import fal_client
from langchain_core.tools import tool
from db.connection import get_db

log = logging.getLogger(__name__)

MODEL = {
    "endpoint": "fal-ai/flux-2-pro",
    "args": {
        "image_size": {"width": 2048, "height": 2048},
        "safety_tolerance": 5,
    },
}


def _generate_image(prompt: str) -> str:
    """Call fal.ai to generate one image. Returns the temporary image URL."""
    os.environ["FAL_KEY"] = os.environ.get("FAL_KEY", "")

    result = fal_client.subscribe(
        MODEL["endpoint"],
        arguments={"prompt": prompt, "num_images": 1, **MODEL["args"]},
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


def _configure_cloudinary():
    """Configure Cloudinary from environment variables (idempotent)."""
    import cloudinary
    cloudinary.config(
        cloud_name=os.environ["CLOUDINARY_CLOUD_NAME"],
        api_key=os.environ["CLOUDINARY_API_KEY"],
        api_secret=os.environ["CLOUDINARY_API_SECRET"],
        secure=True,
    )


def _upload_to_cloudinary(image_bytes: bytes) -> str:
    """Upload image bytes to Cloudinary and return the public URL."""
    import cloudinary.uploader
    _configure_cloudinary()

    # Cloudinary free tier has a 10MB limit; compress large images
    if len(image_bytes) > 9 * 1024 * 1024:
        image_bytes = _compress_image(image_bytes)

    result = cloudinary.uploader.upload(
        image_bytes,
        folder="capaco",
        resource_type="image",
    )
    return result["secure_url"]


def _compress_image(image_bytes: bytes, max_side: int = 2048, quality: int = 85) -> bytes:
    """Compress an image to fit within size limits while preserving quality."""
    from io import BytesIO
    from PIL import Image

    img = Image.open(BytesIO(image_bytes))
    img.thumbnail((max_side, max_side), Image.LANCZOS)
    buf = BytesIO()
    img_format = "JPEG" if img.mode == "RGB" else "PNG"
    if img.mode == "RGBA" and img_format == "JPEG":
        img = img.convert("RGB")
    img.save(buf, format=img_format, quality=quality, optimize=True)
    return buf.getvalue()


def _rehost_image(fal_url: str) -> str:
    """Download image from fal.ai temp URL and rehost on Cloudinary for permanent public URL."""
    resp = requests.get(fal_url, timeout=60)
    resp.raise_for_status()
    return _upload_to_cloudinary(resp.content)


def upscale_and_host(image_url: str) -> str:
    """Upscale an image and rehost it. Returns the permanent public URL."""
    upscaled_url = _upscale_image(image_url)
    return _rehost_image(upscaled_url)


def generate_one(prompt: str) -> str:
    """Generate one image, rehost it (no upscale). Returns permanent URL."""
    fal_url = _generate_image(prompt)
    return _rehost_image(fal_url)


@tool
def generate_and_host_image(prompt: str, post_id: int) -> str:
    """Generate a preview image for a content post using FLUX 2 Pro.
    Upscaling happens after the user approves via Telegram.
    Args:
        prompt: Detailed description of the image to generate.
        post_id: The content queue item ID to update with the generated image.
    """
    try:
        image_url = generate_one(prompt)

        db = get_db()
        db.execute(
            "UPDATE content_queue SET image_url = ?, status = 'pending_approval' WHERE id = ?",
            (image_url, post_id),
        )
        db.commit()

        return f"Image generated for post {post_id}: {image_url}"
    except Exception as e:
        return f"Failed to generate image for post {post_id}: {e}"
