"""Brand configuration loader.

Loads a brand profile from brands/<slug>/config.yaml and exposes it as a
singleton ``brand_config`` that the rest of the codebase can import.

Usage::

    # At startup (main.py / daemon.py):
    from brands.loader import init_brand
    init_brand("capa-co")          # loads brands/capa-co/config.yaml + .env

    # Anywhere else:
    from brands.loader import brand_config
    brand_config.identity.name     # "קאפה ושות׳"
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


# ── Dataclasses mirroring config.yaml structure ──────────────────────────────

@dataclass
class IdentityConfig:
    name: str = ""
    name_en: str = ""
    language: str = "en"
    timezone: str = "UTC"
    market: str = ""
    business_type: str = ""
    target_audience: str = ""


@dataclass
class VoiceConfig:
    tone: str = ""
    caption_style: str = ""
    caption_examples: list[str] = field(default_factory=list)
    bad_caption_examples: list[str] = field(default_factory=list)
    hashtags_default: list[str] = field(default_factory=list)


@dataclass
class VisualConfig:
    style_description: str = ""
    image_base_prompt: str = ""
    image_negative_prompt: str = ""
    subject_constraint: str = ""
    bg_objects: list[str] = field(default_factory=list)
    bg_colors: list[str] = field(default_factory=list)
    color_palette: dict[str, str] = field(default_factory=dict)


@dataclass
class ContentStrategyConfig:
    weekly_feed_posts: int = 5
    weekly_stories: int = 7
    max_posts_per_day: int = 1
    max_stories_per_day: int = 2
    content_pillars: list[str] = field(default_factory=list)
    feed_post_times: list[str] = field(default_factory=lambda: ["07:00", "12:00"])
    story_time: str = "09:00"
    texture_breakers_per_week: int = 2


@dataclass
class ScheduleConfig:
    planning_day: str = "sun"
    culinary_review_hour: int = 6
    culinary_review_minute: int = 30
    planning_hour: int = 7
    design_review_hour: int = 8
    image_generation_hour: int = 9
    publish_hours: str = "6,8,10,12,14,16,18,20"
    analytics_hour: int = 18
    content_review_hour: int = 19
    engagement_days: str = "tue,thu"
    engagement_hour: int = 10
    lead_gen_day: str = "wed"
    lead_gen_hour: int = 10
    token_refresh_days: int = 50
    health_check_minutes: int = 30


@dataclass
class LeadGenerationConfig:
    target_customers: list[str] = field(default_factory=list)
    search_cities: list[str] = field(default_factory=list)


@dataclass
class SeasonalCalendarConfig:
    winter: str = ""
    spring: str = ""
    summer: str = ""
    fall: str = ""
    weekly_rhythm: str = ""


# ── Main BrandConfig ─────────────────────────────────────────────────────────

@dataclass
class BrandConfig:
    slug: str = ""
    brand_dir: Path = field(default_factory=lambda: Path("."))
    identity: IdentityConfig = field(default_factory=IdentityConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    visual: VisualConfig = field(default_factory=VisualConfig)
    content_strategy: ContentStrategyConfig = field(default_factory=ContentStrategyConfig)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    lead_generation: LeadGenerationConfig = field(default_factory=LeadGenerationConfig)
    seasonal_calendar: SeasonalCalendarConfig = field(default_factory=SeasonalCalendarConfig)

    @property
    def content_guide_path(self) -> Path:
        return self.brand_dir / "CONTENT_GUIDE.md"

    @property
    def design_guide_path(self) -> Path:
        return self.brand_dir / "DESIGN.md"

    @property
    def env_path(self) -> Path:
        return self.brand_dir / ".env"

    @classmethod
    def load(cls, slug: str) -> BrandConfig:
        """Load a brand profile from ``brands/<slug>/config.yaml``."""
        brands_root = Path(__file__).resolve().parent
        brand_dir = brands_root / slug
        config_path = brand_dir / "config.yaml"

        if not config_path.exists():
            raise FileNotFoundError(
                f"Brand config not found: {config_path}\n"
                f"Available brands: {', '.join(_list_brands())}"
            )

        with open(config_path, encoding="utf-8") as f:
            raw: dict[str, Any] = yaml.safe_load(f) or {}

        return cls(
            slug=slug,
            brand_dir=brand_dir,
            identity=_load_section(raw.get("identity", {}), IdentityConfig),
            voice=_load_section(raw.get("voice", {}), VoiceConfig),
            visual=_load_section(raw.get("visual", {}), VisualConfig),
            content_strategy=_load_section(raw.get("content_strategy", {}), ContentStrategyConfig),
            schedule=_load_section(raw.get("schedule", {}), ScheduleConfig),
            lead_generation=_load_section(raw.get("lead_generation", {}), LeadGenerationConfig),
            seasonal_calendar=_load_section(raw.get("seasonal_calendar", {}), SeasonalCalendarConfig),
        )


def _load_section(data: dict[str, Any], cls: type) -> Any:
    """Instantiate a dataclass from a dict, ignoring unknown keys."""
    import dataclasses
    valid_fields = {f.name for f in dataclasses.fields(cls)}
    filtered = {k: v for k, v in data.items() if k in valid_fields}
    return cls(**filtered)


def _list_brands() -> list[str]:
    """List available brand slugs (directories under brands/ with a config.yaml)."""
    brands_root = Path(__file__).resolve().parent
    return sorted(
        d.name
        for d in brands_root.iterdir()
        if d.is_dir() and not d.name.startswith("_") and (d / "config.yaml").exists()
    )


# ── Singleton & Initialization ───────────────────────────────────────────────

# Sentinel — an empty BrandConfig that will be replaced by init_brand()
brand_config: BrandConfig = BrandConfig()

_initialized = False

# Snapshot of os.environ captured before any brand env mutation, used to
# reset between set_brand() calls so one brand's overrides don't leak into
# another. Captured lazily on first _reset_env_to_base() call.
_BASE_ENV: dict[str, str] | None = None


def _reset_env_to_base() -> None:
    """Restore os.environ to the snapshot captured before any brand load."""
    global _BASE_ENV
    if _BASE_ENV is None:
        _BASE_ENV = dict(os.environ)
        return
    for k in list(os.environ.keys()):
        if k not in _BASE_ENV:
            del os.environ[k]
    for k, v in _BASE_ENV.items():
        os.environ[k] = v


def _apply_prefixed_env(slug: str) -> None:
    """Apply brand-prefixed env vars by copying them to their unprefixed form.

    Example: ``MILA_TELEGRAM_BOT_TOKEN=xxx`` becomes ``TELEGRAM_BOT_TOKEN=xxx``
    when the active brand is ``mila``. Lets Railway (or any host where per-brand
    .env files aren't shipped) supply brand-specific credentials via a single
    env-var namespace.
    """
    prefix = slug.upper().replace("-", "_") + "_"
    for k, v in list(os.environ.items()):
        if k.startswith(prefix) and len(k) > len(prefix):
            os.environ[k[len(prefix):]] = v


def init_brand(slug: str | None = None) -> BrandConfig:
    """Initialize the global brand_config singleton.

    Resolution order for the brand slug:
    1. Explicit ``slug`` argument
    2. ``--brand <slug>`` CLI argument
    3. ``BRAND`` environment variable
    4. Auto-detect if exactly one brand exists
    """
    global brand_config, _initialized

    if slug is None:
        slug = _resolve_slug()

    # Capture baseline env before any brand mutation.
    _reset_env_to_base()

    brand_config = BrandConfig.load(slug)
    _initialized = True

    # Load brand-specific .env (credentials) if it exists
    if brand_config.env_path.exists():
        from dotenv import load_dotenv
        load_dotenv(brand_config.env_path, override=True)

    _apply_prefixed_env(slug)

    return brand_config


def set_brand(slug: str) -> BrandConfig:
    """Switch the global brand_config to a different brand.

    Used by the daemon when executing tasks for different brands.
    Also loads the brand's .env credentials (or brand-prefixed env vars).
    """
    global brand_config
    _reset_env_to_base()
    brand_config = BrandConfig.load(slug)

    if brand_config.env_path.exists():
        from dotenv import load_dotenv
        load_dotenv(brand_config.env_path, override=True)

    _apply_prefixed_env(slug)

    return brand_config


def load_all_brands() -> list[BrandConfig]:
    """Load all available brand configs (without setting the global singleton)."""
    return [BrandConfig.load(slug) for slug in _list_brands()]


def _resolve_slug() -> str:
    """Determine the brand slug from CLI args or environment."""
    # Check --brand CLI arg
    for i, arg in enumerate(sys.argv):
        if arg == "--brand" and i + 1 < len(sys.argv):
            return sys.argv[i + 1]

    # Check BRAND env var
    env_brand = os.environ.get("BRAND")
    if env_brand:
        return env_brand

    # Auto-detect if exactly one brand exists
    brands = _list_brands()
    if len(brands) == 1:
        return brands[0]

    if not brands:
        raise RuntimeError(
            "No brand profiles found. Create one at brands/<slug>/config.yaml\n"
            "See brands/_template/ for an example."
        )

    raise RuntimeError(
        f"Multiple brands found: {', '.join(brands)}\n"
        f"Specify one with --brand <slug> or BRAND=<slug> env var."
    )
