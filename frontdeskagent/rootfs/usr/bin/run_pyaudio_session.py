#!/usr/bin/env python3
import argparse
import asyncio
import logging
import os

from ha_client import HomeAssistantClient
from session import CameraSession


DEFAULT_MODEL = "gemini-3.1-flash-live-preview"
DEFAULT_PROMPT = {
    "identity": "You are an intelligent door answering assistant for a residential home.",
    "general_instructions": "Greet visitors politely, ask for identity and reason, and help them.",
    "guidelines": "Be professional, concise, and avoid sharing sensitive homeowner details.",
    "language_spoken": "Speak English.",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one FrontDeskAgent session using local PyAudio mic/speaker."
    )
    parser.add_argument(
        "--camera-id",
        default="local_audio",
        help="Logical camera/session id used for state tracking.",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("FRONTDESK_MODEL", DEFAULT_MODEL),
        help="Gemini live model name.",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("GEMINI_API_KEY", os.getenv("FRONTDESK_GEMINI_API_KEY", "")),
        help="Gemini API key. Defaults to GEMINI_API_KEY env var.",
    )
    parser.add_argument(
        "--home-status",
        default="away",
        choices=["home", "away"],
        help="Initial home status prompt context.",
    )
    return parser.parse_args()


async def run_session(args: argparse.Namespace) -> None:
    if not args.api_key:
        raise ValueError(
            "Missing Gemini API key. Provide --api-key or set GEMINI_API_KEY."
        )

    ha_client = HomeAssistantClient(offline_mode=True)
    session = CameraSession(
        camera_id=args.camera_id,
        camera_config={},
        ha_client=ha_client,
        gemini_api_key=args.api_key,
        model=args.model,
        prompt_config=DEFAULT_PROMPT,
    )
    await session.run(home_status=args.home_status)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = parse_args()
    try:
        asyncio.run(run_session(args))
        return 0
    except KeyboardInterrupt:
        print("\nInterrupted by user", flush=True)
        return 130
    except Exception as err:
        print(f"run_pyaudio_session failed: {err}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
