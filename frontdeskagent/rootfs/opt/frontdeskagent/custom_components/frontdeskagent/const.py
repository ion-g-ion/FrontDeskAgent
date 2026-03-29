"""Constants for FrontDeskAgent integration."""

from pathlib import Path

DOMAIN = "frontdeskagent"
DEVICE_ID = "frontdeskagent_device"
CAMERA_INDEX_PATH = Path("/share/frontdeskagent/cameras.json")
VERSION = "0.9.3"
UPDATE_INTERVAL_SECONDS = 30
EVENT_CAMERA_TRIGGERED = "frontdeskagent_camera_triggered"
EVENT_CAMERA_CANCELLED = "frontdeskagent_camera_cancelled"
