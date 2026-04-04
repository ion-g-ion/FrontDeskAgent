import aiohttp
import asyncio
import json
import logging
import os
from typing import Any, AsyncGenerator

logger = logging.getLogger("ha_client")

HOME_STATUS_ENTITIES = [
    "text.frontdeskagent_status",
    "text.frontdeskagent_status_text",
]
CAMERA_STATE_ENTITY_TEMPLATE = "sensor.frontdeskagent_{camera_id}_status"
CAMERA_STATE_ENTITY_TEMPLATE_LEGACY = "sensor.frontdeskagent_{camera_id}_sensor"
PAST_CONVERSATIONS_ENTITY = "todo.frontdeskagent_past_conversations"
NOTIFICATION_ENTITIES = [
    "text.frontdeskagent_notification",
    "text.frontdeskagent_notification_content",
]

class HomeAssistantClient:
    def __init__(self, offline_mode: bool = False):
        self.supervisor_token = os.environ.get("SUPERVISOR_TOKEN", "")
        self.api_base = "http://supervisor/core/api"
        self.ws_url = "ws://supervisor/core/websocket"
        self.offline_mode = offline_mode or os.environ.get("FRONTDESK_HA_OFFLINE", "0") == "1"
        self.fake_server_enabled = (
            os.environ.get("FRONTDESK_FAKE_SERVER", "0") == "1"
            or bool(os.environ.get("FRONTDESK_FAKE_SERVER_URL", ""))
        )
        self.http_timeout = aiohttp.ClientTimeout(total=8)
        self._fake_history: dict[str, list[str]] = {}
        self._fake_last_notification: str = ""
        self.headers = {
            "Authorization": f"Bearer {self.supervisor_token}",
            "Content-Type": "application/json",
        }
        if self.fake_server_enabled:
            logger.info("Fake server mode enabled (in-process logging only, no HTTP).")

    def _ha_available(self) -> bool:
        return bool(self.supervisor_token) and not self.offline_mode

    def _fake_log(self, action: str, payload: dict[str, Any] | None = None) -> None:
        logger.info("[FakeServer] action=%s payload=%s", action, payload or {})

    async def get_home_status(self) -> str:
        """Fetch current value of FrontDeskAgent status entity."""
        if self.fake_server_enabled:
            self._fake_log("get_home_status")
            return "away"

        if not self._ha_available():
            return "away"

        async with aiohttp.ClientSession(timeout=self.http_timeout) as session:
            for entity_id in HOME_STATUS_ENTITIES:
                url = f"{self.api_base}/states/{entity_id}"
                try:
                    async with session.get(url, headers=self.headers) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            return data.get("state", "away")
                        if resp.status != 404:
                            logger.warning(
                                "Failed to get home status from %s: %s",
                                entity_id,
                                resp.status,
                            )
                except Exception as e:
                    logger.error(f"Error fetching home status: {e}")
                    return "away"
        return "away"

    async def set_camera_state(self, camera_id: str, state: str) -> None:
        """Update sensor.frontdeskagent_{camera_id}_status."""
        if self.fake_server_enabled:
            self._fake_log("set_camera_state", {"camera_id": camera_id, "state": state})
            return

        if not self._ha_available():
            return

        payload = {"state": state}
        async with aiohttp.ClientSession(timeout=self.http_timeout) as session:
            for template in (
                CAMERA_STATE_ENTITY_TEMPLATE,
                CAMERA_STATE_ENTITY_TEMPLATE_LEGACY,
            ):
                entity_id = template.format(camera_id=camera_id)
                url = f"{self.api_base}/states/{entity_id}"
                try:
                    async with session.post(url, headers=self.headers, json=payload) as resp:
                        if resp.status in (200, 201):
                            return
                        if resp.status != 404:
                            logger.warning(
                                "Failed to set camera state for %s: %s",
                                entity_id,
                                resp.status,
                            )
                except Exception as e:
                    logger.error(f"Error setting camera state: {e}")
                    return

    async def add_interaction_todo(self, camera_name: str, summary: str) -> None:
        """Add the summary to todo.frontdeskagent_past_conversations."""
        if self.fake_server_enabled:
            self._fake_log(
                "add_interaction_todo",
                {"camera_name": camera_name, "summary": summary},
            )
            self._fake_history.setdefault(camera_name, []).append(summary)
            return

        if not self._ha_available():
            return

        url = f"{self.api_base}/services/todo/add_item"
        payload = {
            "entity_id": PAST_CONVERSATIONS_ENTITY,
            "item": f"{camera_name}: {summary}"
        }
        async with aiohttp.ClientSession(timeout=self.http_timeout) as session:
            try:
                async with session.post(url, headers=self.headers, json=payload) as resp:
                    if resp.status != 200:
                        logger.warning(f"Failed to add todo item: {resp.status}")
            except Exception as e:
                logger.error(f"Error adding todo item: {e}")

    async def set_notification_content(self, message: str) -> None:
        """Write latest notify_owner message to FrontDeskAgent text entity."""
        if self.fake_server_enabled:
            self._fake_log("set_notification_content", {"message": message})
            self._fake_last_notification = message
            return

        if not self._ha_available():
            return

        url = f"{self.api_base}/services/text/set_value"
        async with aiohttp.ClientSession(timeout=self.http_timeout) as session:
            for entity_id in NOTIFICATION_ENTITIES:
                payload = {"entity_id": entity_id, "value": message}
                try:
                    async with session.post(url, headers=self.headers, json=payload) as resp:
                        if resp.status == 200:
                            return
                        if resp.status != 404:
                            logger.warning(
                                "Failed to set notification content for %s: %s",
                                entity_id,
                                resp.status,
                            )
                except Exception as e:
                    logger.error(f"Error setting notification content: {e}")
                    return

    async def fetch_conversation_history(self, camera_name: str, limit: int = 5) -> str:
        """Fetch last N conversation summaries from past conversations list."""
        if self.fake_server_enabled:
            self._fake_log(
                "fetch_conversation_history",
                {"camera_name": camera_name, "limit": limit},
            )
            history_items = self._fake_history.get(camera_name, [])
            if not history_items:
                return "No previous interactions."
            return "\n---\n".join(history_items[-limit:])

        if not self._ha_available():
            return "No history available."

        url = f"{self.api_base}/services/todo/get_items"
        payload = {
            "entity_id": PAST_CONVERSATIONS_ENTITY,
        }
        async with aiohttp.ClientSession(timeout=self.http_timeout) as session:
            try:
                async with session.post(url, headers=self.headers, json=payload) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        # data structure usually: {"todo.frontdeskagent_interactions": {"items": [...]}}
                        # depending on HA version, response can vary. Assuming standard response format:
                        response_data = data
                        items = []
                        if isinstance(response_data, dict):
                            for entity_id, entity_data in response_data.items():
                                if "items" in entity_data:
                                    items.extend(entity_data["items"])
                        
                        # Filter by camera name
                        cam_prefix = f"{camera_name}:"
                        cam_items = [
                            i.get("summary", "")[len(cam_prefix):].strip()
                            for i in items 
                            if i.get("summary", "").startswith(cam_prefix)
                        ]
                        
                        if not cam_items:
                            return "No previous interactions."
                        
                        history = "\n---\n".join(cam_items[-limit:])
                        return history
            except Exception as e:
                logger.error(f"Error fetching conversation history: {e}")
                return "Failed to fetch history."

    async def listen_events(self) -> AsyncGenerator[dict, None]:
        """Connect to WebSocket and yield events."""
        if self.fake_server_enabled:
            logger.info("Fake server event loop active (no external HTTP).")
            while True:
                await asyncio.sleep(60)

        if not self._ha_available():
            logger.warning("HA unavailable; event listener is idle in offline mode.")
            while True:
                await asyncio.sleep(60)

        async with aiohttp.ClientSession(timeout=self.http_timeout) as session:
            try:
                async with session.ws_connect(self.ws_url) as ws:
                    # Auth phase
                    auth_msg = await ws.receive_json()
                    if auth_msg.get("type") == "auth_required":
                        await ws.send_json({
                            "type": "auth",
                            "access_token": self.supervisor_token
                        })
                        auth_ok = await ws.receive_json()
                        if auth_ok.get("type") != "auth_ok":
                            logger.error(f"WebSocket auth failed: {auth_ok}")
                            return
                    
                    logger.info("WebSocket connected and authenticated.")
                    
                    # Subscribe to custom events
                    msg_id = 1
                    for event_type in ["frontdeskagent_camera_triggered", "frontdeskagent_camera_cancelled"]:
                        await ws.send_json({
                            "id": msg_id,
                            "type": "subscribe_events",
                            "event_type": event_type
                        })
                        sub_resp = await ws.receive_json()
                        if not sub_resp.get("success"):
                            logger.error(f"Failed to subscribe to {event_type}: {sub_resp}")
                        else:
                            logger.info(f"Subscribed to {event_type}")
                        msg_id += 1

                    # Listen loop
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            data = json.loads(msg.data)
                            if data.get("type") == "event":
                                event = data.get("event", {})
                                yield event
                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            break
            except Exception as e:
                logger.error(f"WebSocket error: {e}")
                await asyncio.sleep(5) # Delay before caller might retry
