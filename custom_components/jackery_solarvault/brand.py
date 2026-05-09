"""Best-effort sync of HA's cached Jackery brand PNGs into the integration.

Home Assistant 2026.3+ serves custom integration brand images from
``custom_components/<domain>/brand/``. The Jackery brand already exists in the
server-side brand cache as integration domain ``jackery`` on affected HA
systems, while this custom integration uses domain ``jackery_solarvault``.
Copying the cached PNG files into the local ``brand/`` folder keeps the UI on
HA's local brand source instead of shipping hand-made SVG stand-ins.

The sync is intentionally best-effort: read-only mounts, restrictive
permissions, or simply a missing cache must never prevent the integration from
loading. Filesystem errors are logged at debug level.
"""

import logging
from pathlib import Path
import shutil

from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


BRAND_IMAGE_FILENAMES = (
    "icon.png",
    "icon@2x.png",
    "dark_icon.png",
    "dark_icon@2x.png",
    "logo.png",
    "logo@2x.png",
    "dark_logo.png",
    "dark_logo@2x.png",
)
BRAND_CACHE_INTEGRATION_DOMAIN = "jackery"


def _copy_cached_jackery_brand_images(source_dirs: tuple[str, ...]) -> list[str]:
    """Copy official cached Jackery brand PNGs into the custom integration brand folder.

    Brand synchronization is best-effort. Some HA deployments mount custom
    components read-only or with restrictive permissions; a failed cache copy
    must not prevent the integration from loading.
    """
    target_dir = Path(__file__).with_name("brand")
    copied: list[str] = []
    for raw_source_dir in source_dirs:
        source_dir = Path(raw_source_dir)
        if not source_dir.is_dir():
            continue
        try:
            target_dir.mkdir(exist_ok=True)
        except OSError as err:
            _LOGGER.debug(
                "Jackery: cannot prepare local brand cache directory %s: %s",
                target_dir,
                err,
            )
            return copied
        for filename in BRAND_IMAGE_FILENAMES:
            source_file = source_dir / filename
            if not source_file.is_file():
                continue
            target_file = target_dir / filename
            if target_file.is_file():
                try:
                    same_size = source_file.stat().st_size == target_file.stat().st_size
                except OSError:
                    same_size = False
                if same_size:
                    continue
            try:
                shutil.copy2(source_file, target_file)
            except OSError as err:
                _LOGGER.debug(
                    "Jackery: cannot copy cached brand image %s to %s: %s",
                    source_file,
                    target_file,
                    err,
                )
                continue
            copied.append(filename)
        break
    return copied


_BRAND_CACHE_HASS_DATA_KEY = f"{DOMAIN}_brand_cache_synced"


async def _async_ensure_cached_brand_images(hass: HomeAssistant) -> None:
    """Install cached Jackery brand PNGs without blocking the event loop.

    Runs at most once per HA process: ``async_setup`` itself only fires on
    HA boot, but a dedicated flag guards against re-entry from tests or
    manual ``async_setup`` calls. Subsequent boots re-check size cheaply.
    """
    if hass.data.get(_BRAND_CACHE_HASS_DATA_KEY):
        return
    hass.data[_BRAND_CACHE_HASS_DATA_KEY] = True
    source_dirs = (
        hass.config.path(
            ".cache", "brands", "integrations", BRAND_CACHE_INTEGRATION_DOMAIN
        ),
        f"/homeassistant/.cache/brands/integrations/{BRAND_CACHE_INTEGRATION_DOMAIN}",
        f"/config/.cache/brands/integrations/{BRAND_CACHE_INTEGRATION_DOMAIN}",
    )
    try:
        copied = await hass.async_add_executor_job(
            _copy_cached_jackery_brand_images, source_dirs
        )
    except OSError as err:
        _LOGGER.debug("Jackery: cached brand image sync skipped: %s", err)
        return
    if copied:
        _LOGGER.info(
            "Jackery: copied cached brand image(s) into local integration brand folder: %s",
            ", ".join(copied),
        )
