import asyncio
import audioop
import fractions
import logging
import time
from typing import Optional

import aiohttp
import numpy as np
from aiortc import RTCPeerConnection, RTCSessionDescription, MediaStreamTrack
from av import AudioFrame

logger = logging.getLogger("audio")

# Audio config
GEMINI_INPUT_RATE = 16000
GEMINI_OUTPUT_RATE = 24000
GO2RTC_SEND_RATE = 8000
CHANNELS = 1
CHUNK_SIZE = 320
SAMPLE_WIDTH = 2  # int16


class _LocalSpeakerState:
    """Tracks whether local speaker playback is currently active."""

    def __init__(self):
        self.is_speaking = False


def _require_pyaudio():
    try:
        import pyaudio  # type: ignore
    except ImportError as err:
        raise RuntimeError(
            "PyAudio fallback requested but 'pyaudio' is not installed. "
            "Install PyAudio in the runtime image to use local mic/speaker mode."
        ) from err
    return pyaudio


class Go2RTCSpeakerTrack(MediaStreamTrack):
    """Audio track fed by Gemini output queue for WebRTC backchannel."""
    kind = "audio"

    def __init__(self, queue: asyncio.Queue):
        super().__init__()
        self.queue = queue
        self.started = False
        self._resample_state = None
        self._samples_sent = 0
        self._buffer = bytearray()
        self._chunk_samples = GO2RTC_SEND_RATE // 50  # 20ms = 160 samples at 8k
        self._chunk_bytes = self._chunk_samples * SAMPLE_WIDTH

        self._start_time = None
        self._buffering = True
        self._min_buffer_bytes = int(GO2RTC_SEND_RATE * SAMPLE_WIDTH * 0.3)
        self.is_speaking = False

    async def recv(self):
        if self._start_time is None:
            self._start_time = time.monotonic()

        expected_time = self._start_time + (self._samples_sent / GO2RTC_SEND_RATE)
        now = time.monotonic()
        sleep_time = expected_time - now

        if sleep_time > 0:
            await asyncio.sleep(sleep_time)
        elif sleep_time < -0.2:
            self._start_time = time.monotonic() - (self._samples_sent / GO2RTC_SEND_RATE)

        while True:
            try:
                data_24k = self.queue.get_nowait()
                data_8k, self._resample_state = audioop.ratecv(
                    data_24k,
                    SAMPLE_WIDTH,
                    CHANNELS,
                    GEMINI_OUTPUT_RATE,
                    GO2RTC_SEND_RATE,
                    self._resample_state,
                )
                self._buffer.extend(data_8k)
            except asyncio.QueueEmpty:
                break

        if self._buffering and len(self._buffer) < self._min_buffer_bytes:
            try:
                data_24k = await asyncio.wait_for(self.queue.get(), timeout=0.01)
                data_8k, self._resample_state = audioop.ratecv(
                    data_24k, SAMPLE_WIDTH, CHANNELS, GEMINI_OUTPUT_RATE, GO2RTC_SEND_RATE, self._resample_state
                )
                self._buffer.extend(data_8k)
            except asyncio.TimeoutError:
                pass

        if self._buffering and len(self._buffer) >= self._min_buffer_bytes:
            self._buffering = False

        if not self._buffering and len(self._buffer) >= self._chunk_bytes:
            frame_data = self._buffer[:self._chunk_bytes]
            del self._buffer[:self._chunk_bytes]
            is_silence = False
            self.is_speaking = True
        else:
            if not self._buffering and self.started:
                self._buffering = True

            self._resample_state = None
            frame_data = b"\x00" * self._chunk_bytes
            is_silence = True
            self.is_speaking = False

        audio_array = np.frombuffer(frame_data, dtype=np.int16)
        frame = AudioFrame.from_ndarray(audio_array.reshape(1, -1), format="s16", layout="mono")
        frame.sample_rate = GO2RTC_SEND_RATE
        frame.pts = self._samples_sent
        frame.time_base = fractions.Fraction(1, GO2RTC_SEND_RATE)

        self._samples_sent += self._chunk_samples

        if not self.started and not is_silence:
            self.started = True
            logger.info("First Gemini audio frame sent to camera speaker via WebRTC")

        return frame

class CameraAudioIO:
    def __init__(self, camera_config: dict):
        self.host = camera_config.get("go2rtc_host")
        self.api_port = camera_config.get("go2rtc_api_port")
        self.rtsp_port = camera_config.get("go2rtc_rtsp_port")
        self.stream_name = camera_config.get("stream_name")
        self.webrtc_endpoint = f"http://{self.host}:{self.api_port}/api/webrtc?src={self.stream_name}"
        self.rtsp_url = f"rtsp://{self.host}:{self.rtsp_port}/{self.stream_name}"

        self.ffmpeg_process = None
        self.webrtc_pc = None
        self.speaker_track = None

    async def start_mic(self, mic_queue: asyncio.Queue, shutdown_event: asyncio.Event):
        """Receive camera mic audio as 16k mono PCM for Gemini input."""
        ffmpeg_cmd = [
            "ffmpeg",
            "-nostdin",
            "-rtsp_transport", "tcp",
            "-fflags", "nobuffer",
            "-flags", "low_delay",
            "-i", self.rtsp_url,
            "-vn",
            "-acodec", "pcm_s16le",
            "-ar", str(GEMINI_INPUT_RATE),
            "-ac", str(CHANNELS),
            "-f", "s16le",
            "-loglevel", "error",
            "pipe:1",
        ]

        rtsp_restart_count = 0
        while not shutdown_event.is_set():
            logger.info(f"Connecting to RTSP: {self.rtsp_url}")
            self.ffmpeg_process = await asyncio.create_subprocess_exec(
                *ffmpeg_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL,
            )

            async def log_errors():
                while True:
                    if not self.ffmpeg_process or not self.ffmpeg_process.stderr:
                        return
                    line = await self.ffmpeg_process.stderr.readline()
                    if not line:
                        return
                    logger.warning(f"[FFmpeg] {line.decode().strip()}")

            asyncio.create_task(log_errors())

            ended = False
            while not shutdown_event.is_set():
                if not self.ffmpeg_process or not self.ffmpeg_process.stdout:
                    ended = True
                    break
                data = await self.ffmpeg_process.stdout.read(CHUNK_SIZE * SAMPLE_WIDTH)
                if not data:
                    logger.warning("Camera RTSP audio stream ended")
                    ended = True
                    break
                
                # Check AEC / speaker status later in the pipeline or here
                # Put in queue
                if mic_queue.full():
                    try:
                        mic_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                await mic_queue.put({"data": data, "mime_type": "audio/pcm;rate=16000"})

            if self.ffmpeg_process and self.ffmpeg_process.returncode is None:
                self.ffmpeg_process.terminate()
                try:
                    await asyncio.wait_for(self.ffmpeg_process.wait(), timeout=1.5)
                except asyncio.TimeoutError:
                    self.ffmpeg_process.kill()

            if shutdown_event.is_set():
                break

            if ended:
                rtsp_restart_count += 1
                logger.warning(f"Restarting RTSP reader in 1.0s (restart #{rtsp_restart_count})")
                await asyncio.sleep(1.0)

    async def start_speaker(self, speaker_queue: asyncio.Queue, shutdown_event: asyncio.Event):
        """Setup WebRTC backchannel for speaker."""
        logger.info("Connecting go2rtc WebRTC backchannel...")
        self.webrtc_pc = RTCPeerConnection()
        self.speaker_track = Go2RTCSpeakerTrack(speaker_queue)
        self.webrtc_pc.addTrack(self.speaker_track)

        connected = asyncio.Event()

        @self.webrtc_pc.on("connectionstatechange")
        async def on_state_change():
            if not self.webrtc_pc:
                return
            state = self.webrtc_pc.connectionState
            logger.info(f"WebRTC state: {state}")
            if state == "connected":
                connected.set()
            elif state in {"failed", "closed"}:
                shutdown_event.set()

        offer = await self.webrtc_pc.createOffer()
        await self.webrtc_pc.setLocalDescription(offer)

        while self.webrtc_pc.iceGatheringState != "complete":
            await asyncio.sleep(0.1)

        async with aiohttp.ClientSession() as http_session:
            async with http_session.post(
                self.webrtc_endpoint,
                json={"type": "offer", "sdp": self.webrtc_pc.localDescription.sdp},
                headers={"Content-Type": "application/json"},
            ) as response:
                if response.status != 200:
                    raise RuntimeError(f"go2rtc WebRTC error: {await response.text()}")
                answer = await response.json()

        await self.webrtc_pc.setRemoteDescription(
            RTCSessionDescription(sdp=answer["sdp"], type=answer["type"])
        )
        await connected.wait()
        logger.info("WebRTC connected (Gemini audio -> camera speaker)")

    async def cleanup(self):
        logger.info("Cleaning up Audio IO...")
        if self.ffmpeg_process and self.ffmpeg_process.returncode is None:
            self.ffmpeg_process.terminate()
            try:
                await asyncio.wait_for(self.ffmpeg_process.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                self.ffmpeg_process.kill()

        if self.webrtc_pc:
            await self.webrtc_pc.close()

    # Backward-compatible aliases while callers migrate to clearer names.
    async def start_ffmpeg(self, mic_queue: asyncio.Queue, shutdown_event: asyncio.Event):
        await self.start_mic(mic_queue, shutdown_event)

    async def start_webrtc(self, speaker_queue: asyncio.Queue, shutdown_event: asyncio.Event):
        await self.start_speaker(speaker_queue, shutdown_event)


class PyAudioAudioIO:
    """Local microphone/speaker transport using PyAudio."""

    def __init__(self, camera_config: Optional[dict] = None):
        _ = camera_config  # Maintain constructor shape parity with CameraAudioIO.
        self._pa_module = _require_pyaudio()
        self._pa = self._pa_module.PyAudio()
        self._mic_stream = None
        self._speaker_stream = None
        self._speaker_task = None
        self.speaker_track = _LocalSpeakerState()

    async def start_mic(self, mic_queue: asyncio.Queue, shutdown_event: asyncio.Event):
        """Read from local system microphone as 16k mono PCM."""
        logger.info("Starting local PyAudio microphone input")
        self._mic_stream = self._pa.open(
            format=self._pa_module.paInt16,
            channels=CHANNELS,
            rate=GEMINI_INPUT_RATE,
            input=True,
            frames_per_buffer=CHUNK_SIZE,
        )

        while not shutdown_event.is_set():
            try:
                data = await asyncio.to_thread(
                    self._mic_stream.read,
                    CHUNK_SIZE,
                    exception_on_overflow=False,
                )
            except Exception as err:
                logger.warning(f"PyAudio mic read failed: {err}")
                await asyncio.sleep(0.1)
                continue

            if not data:
                continue

            # Gemini expects little-endian int16 PCM; enforce frame alignment.
            if len(data) % SAMPLE_WIDTH != 0:
                data = data[: len(data) - (len(data) % SAMPLE_WIDTH)]
                if not data:
                    continue

            if mic_queue.full():
                try:
                    mic_queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            await mic_queue.put({"data": data, "mime_type": "audio/pcm;rate=16000"})

    async def start_speaker(self, speaker_queue: asyncio.Queue, shutdown_event: asyncio.Event):
        """Initialize speaker output and run playback worker in background."""
        logger.info("Starting local PyAudio speaker output")
        self._speaker_stream = self._pa.open(
            format=self._pa_module.paInt16,
            channels=CHANNELS,
            rate=GEMINI_OUTPUT_RATE,
            output=True,
            frames_per_buffer=GEMINI_OUTPUT_RATE // 50,  # 20ms
        )
        # CameraAudioIO's start_speaker performs setup and returns; mirror that behavior
        # so CameraSession can continue to launch mic+LLM tasks.
        self._speaker_task = asyncio.create_task(
            self._speaker_playback_loop(speaker_queue, shutdown_event)
        )

    async def _speaker_playback_loop(
        self, speaker_queue: asyncio.Queue, shutdown_event: asyncio.Event
    ):
        """Consume Gemini speaker queue and write PCM to local output device."""
        logger.info("PyAudio speaker playback loop started")

        while not shutdown_event.is_set():
            try:
                data = await asyncio.wait_for(speaker_queue.get(), timeout=0.2)
            except asyncio.TimeoutError:
                self.speaker_track.is_speaking = False
                continue
            except Exception as err:
                logger.warning(f"PyAudio speaker queue read failed: {err}")
                self.speaker_track.is_speaking = False
                continue

            if not data:
                self.speaker_track.is_speaking = False
                continue

            self.speaker_track.is_speaking = True
            try:
                await asyncio.to_thread(self._speaker_stream.write, data)
            except Exception as err:
                logger.warning(f"PyAudio speaker write failed: {err}")
            finally:
                self.speaker_track.is_speaking = False

    async def cleanup(self):
        logger.info("Cleaning up PyAudio IO...")
        if self._speaker_task:
            self._speaker_task.cancel()
            await asyncio.gather(self._speaker_task, return_exceptions=True)
            self._speaker_task = None

        try:
            if self._mic_stream:
                await asyncio.to_thread(self._mic_stream.stop_stream)
                await asyncio.to_thread(self._mic_stream.close)
                self._mic_stream = None
        except Exception as err:
            logger.warning(f"PyAudio mic cleanup failed: {err}")

        try:
            if self._speaker_stream:
                await asyncio.to_thread(self._speaker_stream.stop_stream)
                await asyncio.to_thread(self._speaker_stream.close)
                self._speaker_stream = None
        except Exception as err:
            logger.warning(f"PyAudio speaker cleanup failed: {err}")

        try:
            await asyncio.to_thread(self._pa.terminate)
        except Exception as err:
            logger.warning(f"PyAudio terminate failed: {err}")
