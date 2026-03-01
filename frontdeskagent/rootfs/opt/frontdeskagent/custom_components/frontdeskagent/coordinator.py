"""Coordinator for FrontDeskAgent integration."""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CAMERA_INDEX_PATH, DOMAIN, UPDATE_INTERVAL_SECONDS

LOGGER = logging.getLogger(__name__)


def _read_camera_index_file() -> dict[str, Any]:
    if not CAMERA_INDEX_PATH.exists():
        LOGGER.warning("FrontDeskAgent camera index missing at %s", CAMERA_INDEX_PATH)
        return {"device_id": "frontdeskagent", "cameras": []}
    try:
        data = json.loads(CAMERA_INDEX_PATH.read_text(encoding="utf-8"))
        cameras = data.get("cameras", [])
        LOGGER.warning(
            "FrontDeskAgent loaded camera index from %s with %d camera entries",
            CAMERA_INDEX_PATH,
            len(cameras) if isinstance(cameras, list) else 0,
        )
        return data
    except (OSError, json.JSONDecodeError) as err:
        raise UpdateFailed(f"Unable to read camera index file: {err}") from err


class FrontDeskAgentCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetch camera entity metadata exported by the addon."""

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(
            hass,
            LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL_SECONDS),
        )

    async def _async_update_data(self) -> dict[str, Any]:
        return await self.hass.async_add_executor_job(_read_camera_index_file)
