"""Button entities for FrontDeskAgent cameras."""

from __future__ import annotations

from datetime import datetime
import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DEVICE_ID, DOMAIN, EVENT_CAMERA_TRIGGERED, EVENT_CAMERA_CANCELLED
from .coordinator import FrontDeskAgentCoordinator

LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up FrontDeskAgent button entities."""
    coordinator: FrontDeskAgentCoordinator = hass.data[DOMAIN][entry.entry_id]
    known_camera_ids: set[str] = set()

    @callback
    def add_new_camera_entities() -> None:
        cameras = coordinator.data.get("cameras", []) if coordinator.data else []
        LOGGER.warning(
            "FrontDeskAgent button setup refresh: %d cameras in coordinator data",
            len(cameras),
        )
        new_entities: list[FrontDeskCameraButton] = []
        for camera in cameras:
            camera_id = str(camera.get("camera_id", "")).strip()
            if not camera_id or camera_id in known_camera_ids:
                continue
            known_camera_ids.add(camera_id)
            new_entities.append(FrontDeskCameraButton(camera))
            new_entities.append(FrontDeskCameraCancelButton(camera))
            LOGGER.warning("FrontDeskAgent creating button entities for %s", camera_id)
        if new_entities:
            async_add_entities(new_entities)
            LOGGER.warning("FrontDeskAgent added %d new button entities", len(new_entities))
        else:
            LOGGER.warning("FrontDeskAgent found no new button entities to add")

    add_new_camera_entities()
    entry.async_on_unload(coordinator.async_add_listener(add_new_camera_entities))


class FrontDeskCameraButton(ButtonEntity):
    """Triggerable button entity for one camera."""

    _attr_has_entity_name = True
    _attr_entity_registry_enabled_default = True

    def __init__(self, camera: dict[str, Any]) -> None:
        self._camera_id = str(camera["camera_id"])
        self._camera_name = str(camera.get("camera_name", self._camera_id))
        self._attr_unique_id = f"{DOMAIN}_{self._camera_id}_button_v2"
        self._attr_name = self._camera_id
        self._attr_translation_key = "camera_trigger"
        self._attr_extra_state_attributes = {
            "camera_id": self._camera_id,
            "camera_name": self._camera_name,
        }
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, DEVICE_ID)},
            name="FrontDeskAgent",
            manufacturer="FrontDeskAgent",
            model="Door Front Desk Agent",
        )
        LOGGER.warning(
            "FrontDeskAgent button initialized unique_id=%s camera_name=%s",
            self._attr_unique_id,
            self._camera_name,
        )

    async def async_press(self) -> None:
        """Handle button press action."""
        LOGGER.warning("FrontDeskAgent button pressed for camera_id=%s", self._camera_id)
        self._attr_extra_state_attributes = {
            "camera_id": self._camera_id,
            "camera_name": self._camera_name,
            "last_triggered": datetime.utcnow().isoformat(),
        }
        self.hass.bus.async_fire(
            EVENT_CAMERA_TRIGGERED,
            {"camera_id": self._camera_id, "camera_name": self._camera_name},
        )
        self.async_write_ha_state()


class FrontDeskCameraCancelButton(ButtonEntity):
    """Cancel button entity for one camera."""

    _attr_has_entity_name = True
    _attr_entity_registry_enabled_default = True

    def __init__(self, camera: dict[str, Any]) -> None:
        self._camera_id = str(camera["camera_id"])
        self._camera_name = str(camera.get("camera_name", self._camera_id))
        self._attr_unique_id = f"{DOMAIN}_{self._camera_id}_cancel_button"
        self._attr_name = f"{self._camera_id} Cancel"
        self._attr_translation_key = "camera_cancel"
        self._attr_extra_state_attributes = {
            "camera_id": self._camera_id,
            "camera_name": self._camera_name,
        }
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, DEVICE_ID)},
            name="FrontDeskAgent",
            manufacturer="FrontDeskAgent",
            model="Door Front Desk Agent",
        )
        self._attr_icon = "mdi:cancel"
        LOGGER.warning(
            "FrontDeskAgent cancel button initialized unique_id=%s camera_name=%s",
            self._attr_unique_id,
            self._camera_name,
        )

    async def async_press(self) -> None:
        """Handle cancel button press action."""
        LOGGER.warning("FrontDeskAgent cancel button pressed for camera_id=%s", self._camera_id)
        self._attr_extra_state_attributes = {
            "camera_id": self._camera_id,
            "camera_name": self._camera_name,
            "last_cancelled": datetime.utcnow().isoformat(),
        }
        self.hass.bus.async_fire(
            EVENT_CAMERA_CANCELLED,
            {"camera_id": self._camera_id, "camera_name": self._camera_name},
        )
        self.async_write_ha_state()
