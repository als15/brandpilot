"""Parse docs/CONTENT_GUIDE.md and build image prompts from per-dish entries."""

import os
import re
import difflib
from functools import lru_cache
from langchain_core.tools import tool

_GUIDE_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "docs", "CONTENT_GUIDE.md")

BRAND_SUFFIX = (
    "Photorealistic RAW photo, soft natural morning daylight from the left, "
    "warm ivory/cream/sand palette, matte ceramic props, clean negative space, "
    "premium artisanal bakery styling"
)


@lru_cache(maxsize=1)
def _parse_guide() -> dict:
    """Parse the content guide markdown into structured data."""
    with open(_GUIDE_PATH, encoding="utf-8") as f:
        text = f.read()

    # Extract negative prompt
    neg_match = re.search(r"GLOBAL NEGATIVE PROMPT\s*\n\s*\n(.+?)(?:\n\n|\n##)", text, re.DOTALL)
    negative_prompt = neg_match.group(1).strip() if neg_match else ""

    # Extract per-dish prompts: ## Category -> ### Dish -> paragraph
    dishes: dict[str, str] = {}
    categories: dict[str, list[str]] = {}

    current_category = None
    sections = re.split(r"^(#{2,3})\s+(.+)$", text, flags=re.MULTILINE)

    # sections is: [preamble, level, heading, body, level, heading, body, ...]
    i = 1
    while i < len(sections) - 2:
        level = sections[i]
        heading = sections[i + 1].strip()
        body = sections[i + 2].strip()
        i += 3

        if level == "##":
            current_category = heading
            if current_category not in categories:
                categories[current_category] = []
        elif level == "###" and current_category:
            # Body is everything until next heading; take first non-empty paragraph
            prompt = body.split("\n\n")[0].strip()
            if prompt:
                dishes[heading] = prompt
                categories[current_category].append(heading)

    return {
        "dishes": dishes,
        "categories": categories,
        "negative_prompt": negative_prompt,
    }


def get_negative_prompt() -> str:
    return _parse_guide()["negative_prompt"]


def get_menu_items() -> dict[str, list[str]]:
    """Return dish names grouped by category."""
    return _parse_guide()["categories"]


def get_dish_prompt(name: str) -> str | None:
    """Fuzzy-match a dish name and return its expert prompt, or None."""
    guide = _parse_guide()
    dishes = guide["dishes"]

    # Exact match first
    if name in dishes:
        return dishes[name]

    # Case-insensitive exact match
    lower_map = {k.lower(): k for k in dishes}
    if name.lower() in lower_map:
        return dishes[lower_map[name.lower()]]

    # Fuzzy match
    matches = difflib.get_close_matches(name.lower(), lower_map.keys(), n=1, cutoff=0.6)
    if matches:
        return dishes[lower_map[matches[0]]]

    # Substring match — check if any dish name appears within the visual_direction
    for key, original_key in lower_map.items():
        if key in name.lower():
            return dishes[original_key]

    return None


@tool
def build_image_prompt(visual_direction: str) -> str:
    """Build a complete image generation prompt from a visual direction.

    If the visual_direction matches a menu item from the content guide, the expert
    per-dish prompt is used. Otherwise, the raw direction is wrapped with brand styling.

    Args:
        visual_direction: A dish name (e.g. 'Butter Croissant') or free-form image description.
    """
    negative = get_negative_prompt()
    dish_prompt = get_dish_prompt(visual_direction)

    if dish_prompt:
        core = f"{dish_prompt} {BRAND_SUFFIX}"
    else:
        core = f"{visual_direction}, {BRAND_SUFFIX}"

    return f"{core}. Avoid: {negative}"
