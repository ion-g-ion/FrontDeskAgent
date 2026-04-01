import asyncio
import logging
from audio import CameraAudioIO
from llm import GeminiAgent

logger = logging.getLogger("session")
CONVERSATION_TIMEOUT_SECONDS = 15 * 60

class CameraSession:
    def __init__(self, camera_id: str, camera_config: dict, ha_client, gemini_api_key: str, model: str, prompt_config: dict):
        self.camera_id = camera_id
        self.camera_name = camera_config.get("camera_name", camera_id)
        self.ha_client = ha_client

        self.audio_io = CameraAudioIO(camera_config)
        self.llm_agent = GeminiAgent(
            api_key=gemini_api_key,
            model=model,
            prompt_config=prompt_config,
            ha_client=ha_client,
            camera_name=self.camera_name
        )

        self.mic_queue = asyncio.Queue(maxsize=30)
        self.speaker_queue = asyncio.Queue(maxsize=500)
        self.shutdown_event = asyncio.Event()

    async def run(self, home_status: str):
        logger.info(f"Starting session for camera: {self.camera_name} with home_status: {home_status}")
        try:
            await self.ha_client.set_camera_state(self.camera_id, "active")

            # Start WebRTC first to get the speaker track
            await self.audio_io.start_webrtc(self.speaker_queue, self.shutdown_event)

            async with asyncio.TaskGroup() as tg:
                tg.create_task(self.audio_io.start_ffmpeg(self.mic_queue, self.shutdown_event))
                tg.create_task(self.llm_agent.run(self.mic_queue, self.speaker_queue, self.audio_io.speaker_track, home_status))

                # Stop session when one of these happens:
                # 1) LLM finishes naturally, 2) manual cancel sets shutdown_event, 3) hard timeout.
                async def watch_session_lifecycle():
                    timeout_task = asyncio.create_task(asyncio.sleep(CONVERSATION_TIMEOUT_SECONDS))
                    llm_done_task = asyncio.create_task(self.llm_agent.shutdown_requested.wait())
                    shutdown_task = asyncio.create_task(self.shutdown_event.wait())
                    done, pending = await asyncio.wait(
                        {timeout_task, llm_done_task, shutdown_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )

                    if timeout_task in done:
                        logger.warning(
                            f"Session timeout reached ({CONVERSATION_TIMEOUT_SECONDS}s) for camera: {self.camera_name}"
                        )

                    self.shutdown_event.set()
                    self.llm_agent.shutdown_requested.set()

                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)

                tg.create_task(watch_session_lifecycle())
                
        except Exception as e:
            logger.error(f"Error in CameraSession for {self.camera_name}: {e}")
            await self.ha_client.set_camera_state(self.camera_id, "error")
        finally:
            self.shutdown_event.set()
            self.llm_agent.shutdown_requested.set()
            await self.audio_io.cleanup()
            
            # If we didn't end up in error, reset to waiting
            # Give a small delay in case of task cancellation
            await asyncio.sleep(1)
            await self.ha_client.set_camera_state(self.camera_id, "waiting")
            logger.info(f"Session for camera {self.camera_name} closed.")

    async def cancel(self):
        logger.info(f"Cancelling session for camera: {self.camera_name}")
        self.shutdown_event.set()
        self.llm_agent.shutdown_requested.set()
