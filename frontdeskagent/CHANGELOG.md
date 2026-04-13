<!-- https://developers.home-assistant.io/docs/add-ons/presentation#keeping-a-changelog -->

## 0.9.8

- Remove `pyaudio` from add-on install requirements so Home Assistant builds do not fail trying to compile it in the Alpine base image.

## 0.9.7

- Add optional local PyAudio backend (`PyAudioAudioIO`) for microphone/speaker operation when no camera config is provided.
- Rename audio backend runtime methods to clearer, unified names: `start_mic` and `start_speaker` (with backward-compatible aliases kept for camera RTC path).
- Add standalone runner script (`rootfs/usr/bin/run_pyaudio_session.py`) to start a `CameraSession` without Home Assistant event wiring.
- Add Home Assistant client offline/fake mode improvements:
  - graceful default responses when HA is unavailable,
  - in-process fake server behavior (logs payloads and returns dummy values, no outbound HTTP forwarding).
- Improve Gemini Live request robustness:
  - normalize outgoing mic audio payloads as explicit PCM blobs with sample-rate mime metadata,
  - send initial startup prompt through realtime text input path.
- Add explicit logs for model-driven conversation completion (`final_response`) and clear session-end reason logging (model-finished vs timeout vs manual cancel).
- Add `pyaudio` to Python requirements for local audio fallback support.

## 0.9.6

- Add Gemini Live session resumption and reconnect handling for `goAway` and `1011` disconnects.
- Add bounded reconnect backoff to keep conversations running after transient Live API errors.

## 0.9.4

- Switch add-on base image to Python 3.12.
- Improve default prompt formatting using YAML multiline blocks for easier prompt editing.
- Update prompt translation labels to match active prompt keys.

## 0.9.2

- Major structural rewrite introducing an asynchronous core loop.
- Split audio (FFmpeg/WebRTC) and LLM (Gemini) logic into separate decoupled modules.
- Add global `text` entity for Home Assistant to control the agent's context status (e.g. "away").
- Add camera `sensor` entities to display agent status ("waiting", "active", "error").
- Add cancel button for each camera to safely abort active interactions.
- Dynamic prompt generation with split configuration for identity, instructions, and guidelines.
- Integrate tool for fetching conversation history directly from HA Todo List via REST API.
- Replaced WebRTC and raw websocket scripts with reliable HTTP REST/WS `ha_client` module.
