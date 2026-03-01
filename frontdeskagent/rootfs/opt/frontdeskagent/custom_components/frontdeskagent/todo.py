"""Todo list entity for tracking FrontDeskAgent interactions."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.storage import Store

from .const import DEVICE_ID, DOMAIN

LOGGER = logging.getLogger(__name__)

STORAGE_KEY = f"{DOMAIN}.interactions"
STORAGE_VERSION = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the FrontDeskAgent interactions todo list."""
    store = Store[dict[str, Any]](hass, STORAGE_VERSION, STORAGE_KEY)
    entity = FrontDeskAgentTodoList(store)
    await entity.async_load()
    async_add_entities([entity])


class FrontDeskAgentTodoList(TodoListEntity):
    """Todo list that records each camera interaction as an item."""

    _attr_has_entity_name = True
    _attr_supported_features = (
        TodoListEntityFeature.CREATE_TODO_ITEM
        | TodoListEntityFeature.DELETE_TODO_ITEM
        | TodoListEntityFeature.UPDATE_TODO_ITEM
        | TodoListEntityFeature.SET_DESCRIPTION_ON_ITEM
    )

    def __init__(self, store: Store[dict[str, Any]]) -> None:
        self._store = store
        self._items: list[TodoItem] = []
        self._attr_unique_id = f"{DOMAIN}_interactions"
        self._attr_name = "Interactions"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, DEVICE_ID)},
            name="FrontDeskAgent",
            manufacturer="FrontDeskAgent",
            model="Door Front Desk Agent",
        )

    # ------------------------------------------------------------------
    # TodoListEntity interface
    # ------------------------------------------------------------------

    @property
    def todo_items(self) -> list[TodoItem]:
        """Return current todo items."""
        return self._items

    async def async_create_todo_item(self, item: TodoItem) -> None:
        """Add a new interaction item."""
        item.uid = item.uid or uuid.uuid4().hex
        item.status = item.status or TodoItemStatus.NEEDS_ACTION
        self._items.insert(0, item)
        self.async_write_ha_state()
        await self._async_save()

    async def async_update_todo_item(self, item: TodoItem) -> None:
        """Update an existing item (e.g. mark completed)."""
        for idx, existing in enumerate(self._items):
            if existing.uid == item.uid:
                if item.summary is not None:
                    existing.summary = item.summary
                if item.status is not None:
                    existing.status = item.status
                if item.description is not None:
                    existing.description = item.description
                self._items[idx] = existing
                self.async_write_ha_state()
                await self._async_save()
                return

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        """Delete items by UID."""
        uid_set = set(uids)
        self._items = [i for i in self._items if i.uid not in uid_set]
        self.async_write_ha_state()
        await self._async_save()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    async def async_load(self) -> None:
        """Restore items from HA storage."""
        data = await self._store.async_load()
        if not data or not isinstance(data.get("items"), list):
            return
        for entry in data["items"]:
            if not isinstance(entry, dict):
                continue
            try:
                self._items.append(
                    TodoItem(
                        uid=entry.get("uid", uuid.uuid4().hex),
                        summary=entry.get("summary", ""),
                        status=TodoItemStatus(entry["status"])
                        if "status" in entry
                        else TodoItemStatus.NEEDS_ACTION,
                        description=entry.get("description"),
                    )
                )
            except (KeyError, ValueError):
                continue

    async def _async_save(self) -> None:
        """Persist items to HA storage."""
        await self._store.async_save(
            {
                "items": [
                    {
                        "uid": item.uid,
                        "summary": item.summary,
                        "status": item.status.value
                        if item.status
                        else TodoItemStatus.NEEDS_ACTION.value,
                        "description": item.description,
                    }
                    for item in self._items
                ]
            }
        )
