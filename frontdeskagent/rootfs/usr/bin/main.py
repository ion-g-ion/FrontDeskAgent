#!/usr/bin/env python3
"""FrontDeskAgent configuration bridge entrypoint.

This validates add-on options, prepares runtime configuration, and
auto-creates the HA integration config entry via the Supervisor API.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_MODEL = "gemini-3.1-flash-live-preview"
SUPPORTED_MODELS = {
    DEFAULT_MODEL,
    "gemini-live-2.5-flash-native-audio",
    "gemini-2.5-flash-native-audio-preview-12-2025",
}
OPTIONS_PATH = Path(os.getenv("FRONTDESK_OPTIONS_PATH", "/data/options.json"))
CAMERAS_EXPORT_PATH = Path(
    os.getenv("FRONTDESK_CAMERAS_EXPORT_PATH", "/share/frontdeskagent/cameras.json")
)
DOMAIN = "frontdeskagent"
HA_API_BASE = "http://supervisor/core/api"


_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_]+$")


def _as_non_empty_str(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Invalid '{field}': expected a non-empty string.")
    return value.strip()


def _as_identifier(value: Any, field: str) -> str:
    """Validate that value contains only alphanumeric characters and underscores."""
    s = _as_non_empty_str(value, field)
    if not _IDENTIFIER_RE.match(s):
        raise ValueError(
            f"Invalid '{field}': '{s}' must contain only letters, digits, and underscores."
        )
    return s


def _as_int_port(value: Any, field: str) -> int:
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    if not isinstance(value, int) or value <= 0 or value > 65535:
        raise ValueError(f"Invalid '{field}': expected a valid TCP port (1-65535).")
    return value


def load_options(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ValueError(f"Options file not found at '{path}'.")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        raise ValueError(f"Options file is not valid JSON: {err}") from err


def normalize_runtime_config(options: dict[str, Any]) -> dict[str, Any]:
    gemini_api_key = _as_non_empty_str(options.get("gemini_api_key"), "gemini_api_key")
    model = _as_non_empty_str(options.get("model"), "model")
    if model not in SUPPORTED_MODELS:
        raise ValueError(
            f"Invalid 'model': '{model}'. Supported models: {', '.join(sorted(SUPPORTED_MODELS))}."
        )

    cameras = options.get("cameras")
    if not isinstance(cameras, list) or len(cameras) == 0:
        raise ValueError("Invalid 'cameras': at least one camera configuration is required.")

    normalized_cameras: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for idx, camera in enumerate(cameras):
        if not isinstance(camera, dict):
            raise ValueError(f"Invalid 'cameras[{idx}]': expected an object.")
        cam_name = _as_identifier(
            camera.get("camera_name"), f"cameras[{idx}].camera_name"
        )
        if cam_name in seen_names:
            raise ValueError(
                f"Invalid 'cameras[{idx}].camera_name': '{cam_name}' is already used by another camera."
            )
        seen_names.add(cam_name)
        normalized_cameras.append(
            {
                "camera_name": cam_name,
                "go2rtc_host": _as_non_empty_str(
                    camera.get("go2rtc_host"), f"cameras[{idx}].go2rtc_host"
                ),
                "go2rtc_api_port": _as_int_port(
                    camera.get("go2rtc_api_port"), f"cameras[{idx}].go2rtc_api_port"
                ),
                "go2rtc_rtsp_port": _as_int_port(
                    camera.get("go2rtc_rtsp_port"), f"cameras[{idx}].go2rtc_rtsp_port"
                ),
                "stream_name": _as_non_empty_str(
                    camera.get("stream_name"), f"cameras[{idx}].stream_name"
                ),
                "description": str(camera.get("description", "")).strip(),
                "camera_prompt": str(camera.get("camera_prompt", "")).strip(),
            }
        )

    prompt = options.get("prompt", {})
    if not isinstance(prompt, dict):
        raise ValueError("Invalid 'prompt': expected an object.")

    normalized_prompt = {
        "identity": str(prompt.get("identity", "")).strip(),
        "general_instructions": str(prompt.get("general_instructions", "")).strip(),
        "guidelines": str(prompt.get("guidelines", "")).strip(),
        "language_spoken": str(prompt.get("language_spoken", "")).strip(),
    }

    return {
        "gemini": {
            "api_key": gemini_api_key,
            "model": model,
        },
        "cameras": normalized_cameras,
        "prompt": normalized_prompt,
    }


def log_startup(runtime_config: dict[str, Any]) -> None:
    # Do not print secrets such as API keys to logs.
    print("FrontDeskAgent configuration loaded.", flush=True)
    print(f"Model: {runtime_config['gemini']['model']}", flush=True)
    print(f"Cameras configured: {len(runtime_config['cameras'])}", flush=True)
    for idx, camera in enumerate(runtime_config["cameras"], start=1):
        print(
            f"Camera {idx}: name='{camera['camera_name']}', stream='{camera['stream_name']}', "
            f"go2rtc={camera['go2rtc_host']}:{camera['go2rtc_api_port']}/"
            f"{camera['go2rtc_rtsp_port']}",
            flush=True,
        )


def export_camera_entities(runtime_config: dict[str, Any], export_path: Path) -> None:
    export_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "device_id": "frontdeskagent",
        "updated_at": int(time.time()),
        "cameras": [
            {
                "camera_id": camera["camera_name"],
                "camera_name": camera["camera_name"],
            }
            for camera in runtime_config["cameras"]
        ],
    }
    export_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _ha_api_request(
    method: str, path: str, *, data: dict[str, Any] | None = None, token: str
) -> Any:
    """Call the HA Core REST API via the Supervisor proxy."""
    url = f"{HA_API_BASE}{path}"
    body = json.dumps(data).encode("utf-8") if data else None
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method=method,
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def ensure_integration_config_entry() -> None:
    """Auto-create the integration config entry in HA if it doesn't exist.

    Uses the Supervisor proxy to reach the HA Core REST API.  Retries
    several times because HA Core may still be booting when the add-on
    starts.  On first install HA must be restarted once so the custom
    component is discovered; the retries give it a reasonable window.
    """
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        print("SUPERVISOR_TOKEN not available; skipping auto-setup.", flush=True)
        return

    retry_delays = [0, 15, 30, 60, 120]
    for attempt, delay in enumerate(retry_delays, 1):
        if delay:
            print(
                f"Config entry check: attempt {attempt}/{len(retry_delays)}, "
                f"waiting {delay}s ...",
                flush=True,
            )
            time.sleep(delay)

        try:
            entries = _ha_api_request(
                "GET", "/config/config_entries/entry", token=token
            )
            if any(e.get("domain") == DOMAIN for e in entries):
                print("FrontDeskAgent integration already configured in HA.", flush=True)
                return

            result = _ha_api_request(
                "POST",
                "/config/config_entries/flow",
                data={"handler": DOMAIN, "show_advanced_options": False},
                token=token,
            )
            flow_type = result.get("type") if isinstance(result, dict) else None
            if flow_type == "create_entry":
                print("FrontDeskAgent integration auto-configured in HA.", flush=True)
                return
            if flow_type == "abort":
                print(
                    f"Config flow aborted: {result.get('reason', '?')}. "
                    "Integration may already be set up.",
                    flush=True,
                )
                return
            print(f"Unexpected config flow response: {result}", flush=True)

        except urllib.error.HTTPError as err:
            err_body = err.read().decode(errors="replace")
            print(f"HA API HTTP {err.code}: {err_body}", flush=True)
            if err.code in (400, 404):
                print(
                    "The integration is not yet loaded by Home Assistant. "
                    "A Home Assistant restart may be needed.",
                    flush=True,
                )
        except (urllib.error.URLError, OSError) as err:
            print(f"HA API connection error: {err}", flush=True)
        except Exception as err:
            print(f"Unexpected error during auto-setup: {err}", flush=True)

    print(
        "Auto-setup exhausted retries. If this is the first install, "
        "restart Home Assistant, then restart this add-on.",
        flush=True,
    )


import asyncio
from ha_client import HomeAssistantClient
from session import CameraSession

async def run_loop(runtime_config: dict[str, Any]):
    ha_client = HomeAssistantClient()
    active_sessions = {}
    
    logger = logging.getLogger("main_loop")
    logger.info("FrontDeskAgent is ready; listening for WebSocket events.")
    
    try:
        async for event in ha_client.listen_events():
            event_type = event.get("event_type")
            data = event.get("data", {})
            camera_id = data.get("camera_id")
            
            if not camera_id:
                continue

            if event_type == "frontdeskagent_camera_triggered":
                logger.info(f"Trigger received for {camera_id}")
                if camera_id in active_sessions:
                    logger.info(f"Session already active for {camera_id}, ignoring trigger.")
                    continue
                
                # Find camera config
                cam_config = next((c for c in runtime_config["cameras"] if c["camera_name"] == camera_id), None)
                if not cam_config:
                    logger.warning(f"No config found for camera {camera_id}")
                    continue

                home_status = await ha_client.get_home_status()
                
                session = CameraSession(
                    camera_id=camera_id,
                    camera_config=cam_config,
                    ha_client=ha_client,
                    gemini_api_key=runtime_config["gemini"]["api_key"],
                    model=runtime_config["gemini"]["model"],
                    prompt_config=runtime_config["prompt"]
                )
                active_sessions[camera_id] = session
                
                # Run the session in a background task
                async def run_session(cam_id, sess, status):
                    try:
                        await sess.run(status)
                    finally:
                        active_sessions.pop(cam_id, None)

                asyncio.create_task(run_session(camera_id, session, home_status))

            elif event_type == "frontdeskagent_camera_cancelled":
                logger.info(f"Cancel received for {camera_id}")
                if camera_id in active_sessions:
                    await active_sessions[camera_id].cancel()
                else:
                    logger.info(f"No active session found for {camera_id} to cancel.")
                    # Ensure status gets reset anyway
                    await ha_client.set_camera_state(camera_id, "waiting")
    except asyncio.CancelledError:
        logger.info("Main loop cancelled, shutting down sessions...")
        for sess in active_sessions.values():
            await sess.cancel()

def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        options = load_options(OPTIONS_PATH)
        runtime_config = normalize_runtime_config(options)
    except ValueError as err:
        print(f"FrontDeskAgent startup error: {err}", file=sys.stderr, flush=True)
        return 1

    log_startup(runtime_config)
    export_camera_entities(runtime_config, CAMERAS_EXPORT_PATH)
    print(f"Exported camera entity metadata to {CAMERAS_EXPORT_PATH}", flush=True)

    if os.getenv("FRONTDESK_EXIT_AFTER_VALIDATE", "0") == "1":
        print("Validation-only mode complete.", flush=True)
        return 0

    ensure_integration_config_entry()

    try:
        asyncio.run(run_loop(runtime_config))
    except KeyboardInterrupt:
        print("\nInterrupted by user", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
