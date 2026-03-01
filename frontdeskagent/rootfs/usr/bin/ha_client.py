import aiohttp
import asyncio
import json
import logging
import os
from typing import AsyncGenerator

logger = logging.getLogger("ha_client")

class HomeAssistantClient:
    def __init__(self):
        self.supervisor_token = os.environ.get("SUPERVISOR_TOKEN", "")
        self.api_base = "http://supervisor/core/api"
        self.ws_url = "ws://supervisor/core/websocket"
        self.headers = {
            "Authorization": f"Bearer {self.supervisor_token}",
            "Content-Type": "application/json",
        }

    async def get_home_status(self) -> str:
        """Fetch the current value of text.frontdeskagent_status."""
        if not self.supervisor_token:
            return "away"

        url = f"{self.api_base}/states/text.frontdeskagent_status_text"
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url, headers=self.headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get("state", "away")
                    else:
                        logger.warning(f"Failed to get home status: {resp.status}")
                        return "away"
            except Exception as e:
                logger.error(f"Error fetching home status: {e}")
                return "away"

    async def set_camera_state(self, camera_id: str, state: str) -> None:
        """Update sensor.frontdeskagent_{camera_id}_status."""
        if not self.supervisor_token:
            return

        url = f"{self.api_base}/states/sensor.frontdeskagent_{camera_id}_sensor"
        payload = {"state": state}
        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(url, headers=self.headers, json=payload) as resp:
                    if resp.status not in (200, 201):
                        logger.warning(f"Failed to set camera state: {resp.status}")
            except Exception as e:
                logger.error(f"Error setting camera state: {e}")

    async def add_interaction_todo(self, camera_name: str, summary: str) -> None:
        """Add the summary to todo.frontdeskagent_interactions."""
        if not self.supervisor_token:
            return

        url = f"{self.api_base}/services/todo/add_item"
        payload = {
            "entity_id": "todo.frontdeskagent_interactions",
            "item": f"{camera_name}: {summary}"
        }
        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(url, headers=self.headers, json=payload) as resp:
                    if resp.status != 200:
                        logger.warning(f"Failed to add todo item: {resp.status}")
            except Exception as e:
                logger.error(f"Error adding todo item: {e}")

    async def fetch_conversation_history(self, camera_name: str, limit: int = 5) -> str:
        """Fetch the last N interaction summaries from the Todo list."""
        if not self.supervisor_token:
            return "No history available."

        url = f"{self.api_base}/services/todo/get_items"
        payload = {
            "entity_id": "todo.frontdeskagent_interactions",
        }
        async with aiohttp.ClientSession() as session:
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
        if not self.supervisor_token:
            logger.error("No supervisor token, cannot listen to events.")
            return

        async with aiohttp.ClientSession() as session:
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
