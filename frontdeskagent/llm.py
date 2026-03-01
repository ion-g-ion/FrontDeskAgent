import asyncio
from google import genai
from google.genai import types
import pyaudio

client = genai.Client()

# --- pyaudio config ---
FORMAT = pyaudio.paInt16
CHANNELS = 1
SEND_SAMPLE_RATE = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE = 1024

pya = pyaudio.PyAudio()

# --- Tool Function Declarations (for the model) ---
final_response_declaration = {
    "name": "final_response",
    "description": "Call this function when the conversation with the visitor is complete. This signals the end of the door interaction and logs a summary.",
    "parameters": {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "A comprehensive summary of the interaction including: who was at the door (name if provided, role/type), their reason for visiting, any messages they left, deliveries, actions taken, and the outcome of the conversation.",
            },
        },
        "required": ["summary"],
    },
}

notify_owner_declaration = {
    "name": "notify_owner",
    "description": "Send a notification to the homeowner about the visitor or situation at the door. Use this to alert the owner about deliveries, visitors, or urgent matters.",
    "parameters": {
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "The notification message to send to the homeowner describing the visitor, their purpose, or any relevant information.",
            },
            "important": {
                "type": "boolean",
                "description": "Set to true for urgent matters requiring immediate attention (e.g., emergency, family member, important delivery). Set to false for routine notifications (e.g., sales, surveys).",
            },
        },
        "required": ["message", "important"],
    },
}


# --- Tool Function Implementations (executed locally) ---
def final_response(summary: str) -> dict:
    """Execute the final_response tool."""
    print(f"\n{'='*50}")
    print("📋 DOOR INTERACTION SUMMARY")
    print(f"{'='*50}")
    print(f"{summary}")
    print(f"{'='*50}\n")
    return {"status": "logged", "summary": summary}


def notify_owner(message: str, important: bool) -> dict:
    """Execute the notify_owner tool."""
    priority = "🚨 URGENT" if important else "📬 INFO"
    print(f"\n{priority} - Notification to Owner: {message}\n")
    return {"status": "sent", "priority": "high" if important else "normal", "message": message}


# --- Live API config ---
MODEL = "gemini-2.5-flash-native-audio-preview-12-2025"

SYSTEM_INSTRUCTION = """You are an intelligent door answering assistant for a residential home. Your role is to professionally and warmly interact with visitors who ring the doorbell.

**Your Responsibilities:**
1. Greet visitors politely and ask for their identity and purpose of visit
2. Gather relevant information: name, reason for visit, any messages or deliveries
3. Handle different visitor types appropriately:
   - Delivery persons: Confirm package details, provide instructions
   - Friends/Family: Be warm, offer to notify the owner
   - Sales/Solicitors: Be polite but firm, take messages if needed
   - Emergency services: Treat with urgency, notify owner immediately
   - Unknown visitors: Be cautious, gather information before proceeding

**Guidelines:**
- Always be polite, professional, and helpful
- If the homeowner is home, offer to notify them about the visitor
- If the homeowner is away, take detailed messages
- Use notify_owner for important visitors or urgent matters (set important=True for emergencies or expected guests)
- When the conversation naturally concludes, call final_response with a complete summary
- Keep conversations efficient but thorough

**Important:** Always call final_response at the end of every interaction with a summary that includes:
- Who the visitor was (name if provided, role/type)
- Their reason for visiting
- Any messages, deliveries, or actions taken
- The outcome of the interaction"""

TOOLS = [{"function_declarations": [final_response_declaration, notify_owner_declaration]}]

CONFIG = {
    "response_modalities": ["AUDIO"],
    "system_instruction": SYSTEM_INSTRUCTION,
    "tools": TOOLS,
}

audio_queue_output = asyncio.Queue()
audio_queue_mic = asyncio.Queue(maxsize=5)
audio_stream = None
is_speaking = asyncio.Event()  # Flag to track if AI is speaking
is_speaking.set()  # Initially allow sending (not speaking)

async def listen_audio():
    """Listens for audio and puts it into the mic audio queue."""
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
    kwargs = {"exception_on_overflow": False} if __debug__ else {}
    while True:
        data = await asyncio.to_thread(audio_stream.read, CHUNK_SIZE, **kwargs)
        await audio_queue_mic.put({"data": data, "mime_type": "audio/pcm"})

async def send_realtime(session):
    """Sends audio from the mic audio queue to the GenAI session."""
    while True:
        msg = await audio_queue_mic.get()
        # Only send if AI is not currently speaking
        await is_speaking.wait()
        print("📤 Sending audio to model...")
        await session.send_realtime_input(audio=msg)

async def handle_tool_call(session, tool_call):
    """Handle function calls from the model."""
    function_responses = []

    for fc in tool_call.function_calls:
        print(f"🔧 Tool called: {fc.name}")
        print(f"   Arguments: {fc.args}")

        # Execute the appropriate function
        if fc.name == "final_response":
            result = final_response(**fc.args)
        elif fc.name == "notify_owner":
            result = notify_owner(**fc.args)
        else:
            result = {"error": f"Unknown function: {fc.name}"}

        function_response = types.FunctionResponse(
            id=fc.id,
            name=fc.name,
            response=result
        )
        function_responses.append(function_response)

    # Send the function responses back to the model
    await session.send_tool_response(function_responses=function_responses)
    print("📨 Tool response sent to model")


async def receive_audio(session):
    """Receives responses from GenAI and puts audio data into the speaker audio queue."""
    while True:
        turn = session.receive()
        model_started_speaking = False
        async for response in turn:
            # Handle tool calls
            if response.tool_call:
                await handle_tool_call(session, response.tool_call)
                continue

            if (response.server_content and response.server_content.model_turn):
                for part in response.server_content.model_turn.parts:
                    if part.inline_data and isinstance(part.inline_data.data, bytes):
                        # Signal that AI is speaking (mute mic)
                        if not model_started_speaking:
                            print("🎤 Model started speaking (mic muted)")
                            model_started_speaking = True
                            is_speaking.clear()
                        audio_queue_output.put_nowait(part.inline_data.data)

        # Empty the queue on interruption to stop playback
        if not audio_queue_output.empty():
            print("⏹️  Interruption detected - clearing playback queue")
        while not audio_queue_output.empty():
            audio_queue_output.get_nowait()

        # Signal that AI finished speaking (unmute mic)
        if model_started_speaking:
            print("✅ Model finished speaking (mic unmuted)")
        is_speaking.set()

async def play_audio():
    """Plays audio from the speaker audio queue."""
    stream = await asyncio.to_thread(
        pya.open,
        format=FORMAT,
        channels=CHANNELS,
        rate=RECEIVE_SAMPLE_RATE,
        output=True,
    )
    while True:
        bytestream = await audio_queue_output.get()
        print("🔊 Playing audio chunk...")
        await asyncio.to_thread(stream.write, bytestream)

        # If queue is empty, wait a bit for any remaining audio to finish, then unmute
        if audio_queue_output.empty():
            print("⏸️  Audio queue empty, waiting for playback to finish...")
            await asyncio.sleep(0.5)  # Buffer time for audio to finish playing
            if audio_queue_output.empty():  # Check again to be sure
                print("✅ Playback complete (mic unmuted)")
                is_speaking.set()

async def run(home_flag: str = "away"):
    """
    Main function to run the door answering agent.

    Args:
        home_flag: Status of the homeowner - "home" or "away"
    """
    try:
        async with client.aio.live.connect(
            model=MODEL, config=CONFIG
        ) as live_session:
            print("🚪 Door Answering Agent Connected")
            print(f"📍 Homeowner status: {home_flag}")
            print("-" * 40)

            # Send the initial trigger message
            initial_message = f"Someone rang the doorbell. The homeowner is {home_flag}."
            print(f"🔔 Trigger: {initial_message}")
            await live_session.send_client_content(
                turns={"parts": [{"text": initial_message}]}
            )

            async with asyncio.TaskGroup() as tg:
                tg.create_task(send_realtime(live_session))
                tg.create_task(listen_audio())
                tg.create_task(receive_audio(live_session))
                tg.create_task(play_audio())
    except asyncio.CancelledError:
        pass
    finally:
        if audio_stream:
            audio_stream.close()
        pya.terminate()
        print("\nConnection closed.")


if __name__ == "__main__":
    import sys

    # Parse command line argument for home status
    home_status = "away"
    if len(sys.argv) > 1:
        home_status = sys.argv[1].lower()
        if home_status not in ["home", "away"]:
            print("Usage: python test_gemini_live.py [home|away]")
            print("  home  - Homeowner is present")
            print("  away  - Homeowner is not present (default)")
            sys.exit(1)

    print("\n🏠 Door Answering Agent")
    print("=" * 40)

    try:
        asyncio.run(run(home_flag=home_status))
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
