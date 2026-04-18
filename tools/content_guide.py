"""Parse brand CONTENT_GUIDE.md and build image prompts from per-dish entries."""

import re
import random
import difflib
from functools import lru_cache
from langchain_core.tools import tool

import brands.loader as _brand_loader


def _random_bg_objects() -> str:
    """Generate a randomized background-objects clause for image prompts."""
    bg_objects = _brand_loader.brand_config.visual.bg_objects
    bg_colors = _brand_loader.brand_config.visual.bg_colors
    if not bg_objects or not bg_colors:
        return ""
    count = random.randint(1, 5)
    chosen = random.sample(bg_objects, min(count, len(bg_objects)))
    color = random.choice(bg_colors)
    objects_str = ", ".join(chosen)
    return (
        f"with {count} small decorative background objects in {color} tones "
        f"({objects_str}) "
        "placed behind or to the side of the main subject, never in front — "
        "the food remains the hero and center of the frame"
    )


def _brand_suffix() -> str:
    base = _brand_loader.brand_config.visual.image_base_prompt.strip()
    bg = _random_bg_objects()
    return f"{base}, {bg}" if bg else base


@lru_cache(maxsize=None)
def _parse_guide(guide_path: str) -> dict:
    """Parse the content guide markdown into structured data."""
    with open(guide_path, encoding="utf-8") as f:
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


def _get_guide() -> dict:
    """Load and parse the current brand's content guide."""
    return _parse_guide(str(_brand_loader.brand_config.content_guide_path))


def get_negative_prompt() -> str:
    return _get_guide()["negative_prompt"]


def get_menu_items() -> dict[str, list[str]]:
    """Return dish names grouped by category."""
    return _get_guide()["categories"]


def get_dish_prompt(name: str) -> str | None:
    """Fuzzy-match a dish name and return its expert prompt, or None."""
    guide = _get_guide()
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
    per-dish prompt is used. Otherwise, the raw direction is wrapped with brand styling
    and an ice-cream-only constraint to prevent off-brand food from appearing.

    Args:
        visual_direction: A dish name (e.g. 'Pistachio + Vanilla Waffle Cone') or free-form image description.
    """
    negative = get_negative_prompt()
    dish_prompt = get_dish_prompt(visual_direction)

    if dish_prompt:
        core = f"{dish_prompt} {_brand_suffix()}"
    else:
        # Enforce ice-cream-only constraint for free-form directions that
        # didn't match a known menu item.
        ice_cream_guard = (
            "This is an ice cream and gelato brand ONLY. "
            "The only food shown must be ice cream, gelato, sorbet, waffle cones, "
            "or ice cream toppings. Do not depict any other food items."
        )
        core = f"{visual_direction}, {ice_cream_guard} {_brand_suffix()}"

    return f"{core}. Avoid: {negative}"
