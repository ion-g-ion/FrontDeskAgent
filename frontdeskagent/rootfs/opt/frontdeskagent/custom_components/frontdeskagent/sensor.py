"""Sensor entities for FrontDeskAgent cameras."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DEVICE_ID, DOMAIN
from .coordinator import FrontDeskAgentCoordinator

LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up FrontDeskAgent sensor entities."""
    coordinator: FrontDeskAgentCoordinator = hass.data[DOMAIN][entry.entry_id]
    known_camera_ids: set[str] = set()

    @callback
    def add_new_camera_entities() -> None:
        cameras = coordinator.data.get("cameras", []) if coordinator.data else []
        new_entities: list[FrontDeskCameraSensor] = []
        for camera in cameras:
            camera_id = str(camera.get("camera_id", "")).strip()
            if not camera_id or camera_id in known_camera_ids:
                continue
            known_camera_ids.add(camera_id)
            new_entities.append(FrontDeskCameraSensor(camera))
            LOGGER.warning("FrontDeskAgent creating sensor entity for %s", camera_id)
        if new_entities:
            async_add_entities(new_entities)

    add_new_camera_entities()
    entry.async_on_unload(coordinator.async_add_listener(add_new_camera_entities))


class FrontDeskCameraSensor(SensorEntity):
    """Sensor entity for one camera status."""

    _attr_has_entity_name = True

    def __init__(self, camera: dict[str, Any]) -> None:
        self._camera_id = str(camera["camera_id"])
        self._camera_name = str(camera.get("camera_name", self._camera_id))
        self._attr_unique_id = f"{DOMAIN}_{self._camera_id}_sensor"
        self._attr_name = f"{self._camera_id} Status"
        self._attr_translation_key = "camera_status"
        self._attr_native_value = "waiting"
        self._attr_icon = "mdi:robot"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, DEVICE_ID)},
            name="FrontDeskAgent",
            manufacturer="FrontDeskAgent",
            model="Door Front Desk Agent",
        )
