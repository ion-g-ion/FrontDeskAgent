import asyncio
import logging
from audio import CameraAudioIO, PyAudioAudioIO
from llm import GeminiAgent

logger = logging.getLogger("session")
CONVERSATION_TIMEOUT_SECONDS = 15 * 60

class CameraSession:
    def __init__(self, camera_id: str, camera_config: dict, ha_client, gemini_api_key: str, model: str, prompt_config: dict):
        self.camera_id = camera_id
        self.camera_name = camera_config.get("camera_name", camera_id)
        self.ha_client = ha_client
        self._run_task = None
        self._tasks = set()

        if camera_config:
            self.audio_io = CameraAudioIO(camera_config)
        else:
            logger.info("No camera config provided; using local PyAudio audio backend.")
            self.audio_io = PyAudioAudioIO(camera_config)
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
        self._run_task = asyncio.current_task()
        logger.info(f"Starting session for camera: {self.camera_name} with home_status: {home_status}")
        try:
            await self.ha_client.set_camera_state(self.camera_id, "active")

            # Start WebRTC first to get the speaker track
            await self.audio_io.start_speaker(self.speaker_queue, self.shutdown_event)

            mic_task = asyncio.create_task(
                self.audio_io.start_mic(self.mic_queue, self.shutdown_event)
            )
            llm_task = asyncio.create_task(
                self.llm_agent.run(
                    self.mic_queue,
                    self.speaker_queue,
                    self.audio_io.speaker_track,
                    home_status,
                )
            )
            timeout_task = asyncio.create_task(
                asyncio.sleep(CONVERSATION_TIMEOUT_SECONDS)
            )
            llm_done_task = asyncio.create_task(
                self.llm_agent.shutdown_requested.wait()
            )
            shutdown_task = asyncio.create_task(self.shutdown_event.wait())
            self._tasks.update(
                {mic_task, llm_task, timeout_task, llm_done_task, shutdown_task}
            )

            done, pending = await asyncio.wait(
                {timeout_task, llm_done_task, shutdown_task},
                return_when=asyncio.FIRST_COMPLETED,
            )

            if timeout_task in done:
                logger.warning(
                    f"Session timeout reached ({CONVERSATION_TIMEOUT_SECONDS}s) for camera: {self.camera_name}"
                )
                await self.ha_client.add_interaction_todo(self.camera_name, "Session ended due to timeout without a final response from the agent.")
            elif llm_done_task in done:
                logger.info(
                    "Session ending because model requested shutdown for camera: %s",
                    self.camera_name,
                )
            elif shutdown_task in done:
                logger.info(
                    "Session ending due to external shutdown/cancel for camera: %s",
                    self.camera_name,
                )
                await self.ha_client.add_interaction_todo(self.camera_name, "Session was cancelled prematurely before a final response could be logged.")

            self.shutdown_event.set()
            self.llm_agent.shutdown_requested.set()

            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

            # Actively cancel the camera/Gemini workers so cancel frees the camera
            # immediately instead of waiting for blocked IO to unwind naturally.
            for task in (mic_task, llm_task):
                task.cancel()
            try:
                await asyncio.wait_for(asyncio.gather(mic_task, llm_task, return_exceptions=True), timeout=2.0)
            except asyncio.TimeoutError:
                logger.warning(f"Timeout waiting for worker tasks to cancel for camera: {self.camera_name}")
            self._tasks.clear()
        except Exception as e:
            logger.error(f"Error in CameraSession for {self.camera_name}: {e}")
            await self.ha_client.set_camera_state(self.camera_id, "error")
        finally:
            self.shutdown_event.set()
            self.llm_agent.shutdown_requested.set()
            if self._tasks:
                for task in self._tasks:
                    task.cancel()
                try:
                    await asyncio.wait_for(asyncio.gather(*self._tasks, return_exceptions=True), timeout=2.0)
                except asyncio.TimeoutError:
                    logger.warning(f"Timeout waiting for leftover tasks to cancel for camera: {self.camera_name}")
                self._tasks.clear()
            await self.audio_io.cleanup()
            
            # If we didn't end up in error, reset to waiting
            # Give a small delay in case of task cancellation
            await asyncio.sleep(1)
            await self.ha_client.set_camera_state(self.camera_id, "waiting")
            logger.info(f"Session for camera {self.camera_name} closed.")
            self._run_task = None

    async def cancel(self):
        logger.info(f"Cancelling session for camera: {self.camera_name}")
        self.shutdown_event.set()
        self.llm_agent.shutdown_requested.set()
        if self._run_task and not self._run_task.done():
            self._run_task.cancel()
