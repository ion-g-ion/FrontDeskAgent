# Home Assistant Add-on: FrontDeskAgent

## How to use

This add-on provides FrontDeskAgent functionality for Home Assistant.

Configure your Gemini API key, model, cameras, and prompt sections in the add-on configuration UI.

## Camera settings fields

Each item in `cameras` represents one camera stream.

- `camera_name`: Friendly camera name used to build the per-camera Home Assistant entity.
- `go2rtc_host`: Hostname or IP address where your go2rtc service runs.
- `go2rtc_api_port`: go2rtc HTTP/WebRTC API port (commonly `1984`).
- `go2rtc_rtsp_port`: go2rtc RTSP port used for stream transport (commonly `8554`).
- `stream_name`: Stream name defined in go2rtc (for example `doorbell`).
- `description`: Natural-language description of what this camera sees; this is passed to the LLM as context.
- `camera_prompt`: Extra camera-specific instructions for the LLM (for example behavior for this view).
