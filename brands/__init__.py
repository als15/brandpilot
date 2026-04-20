"""Brand configuration module — load brand profiles from brands/<slug>/config.yaml.

Note: ``brand_config`` is a module-level mutable global in ``brands.loader`` and is
rebound by ``set_brand()``. Do NOT re-export it here (or import it with
``from brands.loader import brand_config``) — that captures the value at import
time and goes stale after the first ``set_brand()`` call. Always access it via
``brands.loader.brand_config`` so the lookup is dynamic.
"""

from brands.loader import BrandConfig, init_brand, set_brand, load_all_brands

__all__ = ["BrandConfig", "init_brand", "set_brand", "load_all_brands"]
