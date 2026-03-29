<!-- https://developers.home-assistant.io/docs/add-ons/presentation#keeping-a-changelog -->

## 0.9.2

- Major structural rewrite introducing an asynchronous core loop.
- Split audio (FFmpeg/WebRTC) and LLM (Gemini) logic into separate decoupled modules.
- Add global `text` entity for Home Assistant to control the agent's context status (e.g. "away").
- Add camera `sensor` entities to display agent status ("waiting", "active", "error").
- Add cancel button for each camera to safely abort active interactions.
- Dynamic prompt generation with split configuration for identity, instructions, and guidelines.
- Integrate tool for fetching conversation history directly from HA Todo List via REST API.
- Replaced WebRTC and raw websocket scripts with reliable HTTP REST/WS `ha_client` module.
