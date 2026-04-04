# FrontDeskAgent

Home Assistant add-on repository for FrontDeskAgent.
FrontDeskAgent helps you turn camera events into natural-language summaries and assistant-style responses, so you can monitor entrances with less noise and faster context.
It uses live model inference at runtime, not pre-scripted templates.

Add-on documentation: <https://developers.home-assistant.io/docs/add-ons>

[![Open your Home Assistant instance and show the add add-on repository dialog with a specific repository URL pre-filled.](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fion-g-ion%2FFrontDeskAgent)

## Add-ons

This repository contains the following add-ons:

### [FrontDeskAgent](./frontdeskagent)

![Supports aarch64 Architecture][aarch64-shield]
![Supports amd64 Architecture][amd64-shield]

_AI-assisted front desk workflow for Home Assistant._

### What it does

- Connects camera streams (via go2rtc) to the FrontDeskAgent runtime.
- Uses live LLM calls to generate scene-aware responses in real time.
- Supports camera-specific descriptions and prompts for better behavior.

### Quick start

1. Add this repository to Home Assistant.
2. Install the `FrontDeskAgent` add-on.
3. Configure your API/model settings and at least one camera.
4. Start the add-on and verify entities/services in Home Assistant.

For field-level configuration details, see [`frontdeskagent/DOCS.md`](./frontdeskagent/DOCS.md).

[aarch64-shield]: https://img.shields.io/badge/aarch64-yes-green.svg
[amd64-shield]: https://img.shields.io/badge/amd64-yes-green.svg
