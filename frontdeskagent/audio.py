"""
Two-way audio streaming via go2rtc (Hybrid Mode)

This script connects to a camera through go2rtc for bidirectional audio:
- SEND: WebRTC via go2rtc (laptop mic → camera speaker)
- RECEIVE: FFmpeg/RTSP via go2rtc (camera mic → laptop speaker)

Why hybrid?
- WebRTC sending works great with go2rtc's backchannel support
- WebRTC receiving has issues with aiortc, so we use FFmpeg/RTSP instead
"""

import asyncio
import pyaudio
import aiohttp
import numpy as np
from aiortc import RTCPeerConnection, RTCSessionDescription, MediaStreamTrack
from av import AudioFrame

# --- pyaudio config ---
FORMAT = pyaudio.paInt16
CHANNELS = 1
SEND_SAMPLE_RATE = 8000    # PCMU/8000 for back-channel
RECEIVE_SAMPLE_RATE = 16000  # AAC/16000 from camera
CHUNK_SIZE = 1024

pya = pyaudio.PyAudio()

# --- go2rtc config ---
GO2RTC_HOST = "192.168.64.4"
GO2RTC_API_PORT = "1984"
GO2RTC_RTSP_PORT = "8554"
CAMERA_STREAM_NAME = "doorbell"

# URLs
WEBRTC_ENDPOINT = f"http://{GO2RTC_HOST}:{GO2RTC_API_PORT}/api/webrtc?src={CAMERA_STREAM_NAME}"
RTSP_URL = f"rtsp://{GO2RTC_HOST}:{GO2RTC_RTSP_PORT}/{CAMERA_STREAM_NAME}"

# Audio queues
audio_queue_output = asyncio.Queue()  # Camera → Speaker
audio_queue_mic = asyncio.Queue(maxsize=10)  # Mic → Camera

# Global state
audio_stream = None
ffmpeg_process = None
webrtc_pc = None
mic_track = None


class MicrophoneTrack(MediaStreamTrack):
    """Audio track that captures from microphone for WebRTC"""
    kind = "audio"

    def __init__(self, queue: asyncio.Queue):
        super().__init__()
        self.queue = queue
        self.started = False

    async def recv(self):
        """Get audio frame from queue"""
        data = await self.queue.get()

        # Convert to AudioFrame
        audio_array = np.frombuffer(data, dtype=np.int16)
        frame = AudioFrame.from_ndarray(
            audio_array.reshape(1, -1),
            format='s16',
            layout='mono'
        )
        frame.sample_rate = SEND_SAMPLE_RATE
        frame.pts = int(asyncio.get_event_loop().time() * SEND_SAMPLE_RATE)

        if not self.started:
            self.started = True
            print("📤 First audio frame sent to camera via WebRTC")

        return frame


async def listen_audio():
    """Captures audio from microphone and puts it into the queue."""
    global audio_stream

    mic_info = pya.get_default_input_device_info()
    audio_stream = await asyncio.to_thread(
        pya.open,
        format=FORMAT,
        channels=CHANNELS,
        rate=SEND_SAMPLE_RATE,
        input=True,
        input_device_index=mic_info["index"],
        frames_per_buffer=CHUNK_SIZE,
    )
    print(f"🎤 Microphone opened: {mic_info['name']}")

    kwargs = {"exception_on_overflow": False}
    chunks = 0

    while True:
        data = await asyncio.to_thread(audio_stream.read, CHUNK_SIZE, **kwargs)
        await audio_queue_mic.put(data)
        chunks += 1
        if chunks % 200 == 0:
            print(f"🎤 Captured {chunks} chunks")


async def send_audio_webrtc():
    """Sends audio to camera via go2rtc WebRTC."""
    global webrtc_pc, mic_track

    print("📡 Connecting to go2rtc WebRTC...")

    webrtc_pc = RTCPeerConnection()
    mic_track = MicrophoneTrack(audio_queue_mic)
    webrtc_pc.addTrack(mic_track)

    # Track connection state
    connected = asyncio.Event()

    @webrtc_pc.on("connectionstatechange")
    async def on_state_change():
        state = webrtc_pc.connectionState
        print(f"🔗 WebRTC: {state}")
        if state == "connected":
            connected.set()
        elif state == "failed":
            print("❌ WebRTC connection failed")

    # Create and send offer
    offer = await webrtc_pc.createOffer()
    await webrtc_pc.setLocalDescription(offer)

    # Wait for ICE gathering
    while webrtc_pc.iceGatheringState != "complete":
        await asyncio.sleep(0.1)

    # Send offer to go2rtc
    async with aiohttp.ClientSession() as session:
        async with session.post(
            WEBRTC_ENDPOINT,
            json={"type": "offer", "sdp": webrtc_pc.localDescription.sdp},
            headers={"Content-Type": "application/json"}
        ) as response:
            if response.status != 200:
                raise Exception(f"go2rtc error: {await response.text()}")

            answer = await response.json()
            await webrtc_pc.setRemoteDescription(
                RTCSessionDescription(sdp=answer["sdp"], type=answer["type"])
            )

    print("✅ WebRTC connected (sending audio to camera)")

    # Keep alive
    await connected.wait()
    while webrtc_pc.connectionState == "connected":
        await asyncio.sleep(1)


async def receive_audio_rtsp():
    """Receives audio from camera via FFmpeg/RTSP."""
    global ffmpeg_process

    print(f"📥 Connecting to RTSP: {RTSP_URL}")

    ffmpeg_cmd = [
        'ffmpeg',
        '-rtsp_transport', 'tcp',
        '-i', RTSP_URL,
        '-vn',  # No video
        '-acodec', 'pcm_s16le',
        '-ar', str(RECEIVE_SAMPLE_RATE),
        '-ac', str(CHANNELS),
        '-f', 's16le',
        '-loglevel', 'error',
        'pipe:1'
    ]

    ffmpeg_process = await asyncio.create_subprocess_exec(
        *ffmpeg_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.DEVNULL
    )

    print(f"✅ FFmpeg started (receiving audio from camera)")

    # Read stderr in background
    async def log_errors():
        while True:
            line = await ffmpeg_process.stderr.readline()
            if not line:
                break
            print(f"[FFmpeg] {line.decode().strip()}")

    asyncio.create_task(log_errors())

    chunks = 0
    while True:
        data = await ffmpeg_process.stdout.read(CHUNK_SIZE * 2)
        if not data:
            print("⚠️  FFmpeg stream ended")
            break

        await audio_queue_output.put(data)
        chunks += 1
        if chunks == 1:
            print("📥 First audio chunk from camera")
        if chunks % 200 == 0:
            print(f"📥 Received {chunks} chunks from camera")


async def play_audio():
    """Plays audio from the queue to speakers."""
    stream = await asyncio.to_thread(
        pya.open,
        format=FORMAT,
        channels=CHANNELS,
        rate=RECEIVE_SAMPLE_RATE,
        output=True,
    )
    print(f"🔊 Speaker initialized at {RECEIVE_SAMPLE_RATE}Hz")

    chunks = 0
    while True:
        data = await audio_queue_output.get()
        await asyncio.to_thread(stream.write, data)
        chunks += 1
        if chunks == 1:
            print("🔊 Playing audio...")
        if chunks % 200 == 0:
            print(f"🔊 Played {chunks} chunks")


async def run():
    """Main function - runs all audio tasks."""
    global audio_stream, ffmpeg_process, webrtc_pc

    print(f"\n{'='*50}")
    print("  🎬 TWO-WAY AUDIO - go2rtc Hybrid")
    print(f"{'='*50}\n")
    print(f"📍 go2rtc: {GO2RTC_HOST}")
    print(f"📻 Stream: {CAMERA_STREAM_NAME}")
    print(f"📤 Send: WebRTC → Camera")
    print(f"📥 Receive: RTSP/FFmpeg → Speaker\n")

    try:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(listen_audio())
            tg.create_task(send_audio_webrtc())
            tg.create_task(receive_audio_rtsp())
            tg.create_task(play_audio())
            print("✅ All tasks running\n")

    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print("\n🧹 Cleaning up...")

        if audio_stream:
            audio_stream.close()

        if ffmpeg_process and ffmpeg_process.returncode is None:
            ffmpeg_process.terminate()
            try:
                await asyncio.wait_for(ffmpeg_process.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                ffmpeg_process.kill()

        if webrtc_pc:
            await webrtc_pc.close()

        pya.terminate()
        print("👋 Done\n")


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\n⚠️  Interrupted")
