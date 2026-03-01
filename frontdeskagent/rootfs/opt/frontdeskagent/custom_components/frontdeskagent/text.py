"""Text entity for FrontDeskAgent home status."""

from __future__ import annotations

import logging

from homeassistant.components.text import TextEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DEVICE_ID, DOMAIN

LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up FrontDeskAgent text entity."""
    async_add_entities([FrontDeskAgentStatusText()])


class FrontDeskAgentStatusText(TextEntity):
    """Text entity for the global home presence status."""

    _attr_has_entity_name = True
    _attr_unique_id = f"{DOMAIN}_status_text"
    _attr_name = "Status"
    _attr_translation_key = "agent_status"
    _attr_native_value = "away"
    _attr_icon = "mdi:home-account"

    def __init__(self) -> None:
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, DEVICE_ID)},
            name="FrontDeskAgent",
            manufacturer="FrontDeskAgent",
            model="Door Front Desk Agent",
        )

    async def async_set_value(self, value: str) -> None:
        """Set the text value."""
        self._attr_native_value = value
        self.async_write_ha_state()
