import asyncio
import logging
from google import genai
from google.genai import types

logger = logging.getLogger("llm")
GEMINI_INPUT_SAMPLE_RATE = 16000

# Tool Declarations
final_response_declaration = {
    "name": "final_response",
    "description": (
        "Call this function when the conversation with the visitor is complete. "
        "This signals the end of the door interaction and logs a summary. "
        "Call only when the conversation is finished and no further conversation is needed."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": (
                    "A comprehensive summary of the interaction including: who was at the door "
                    "(name if provided, role/type), their reason for visiting, any messages they left, "
                    "deliveries, actions taken, and the outcome of the conversation."
                ),
            },
        },
        "required": ["summary"],
    },
}

notify_owner_declaration = {
    "name": "notify_owner",
    "description": (
        "Send a notification to the homeowner about the visitor or situation at the door. "
        "Use this to alert the owner about deliveries, visitors, or urgent matters."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": (
                    "The notification message to send to the homeowner describing the visitor, "
                    "their purpose, or any relevant information."
                ),
            },
            "important": {
                "type": "boolean",
                "description": (
                    "Set to true for urgent matters requiring immediate attention "
                    "(e.g., emergency, family member, important delivery). "
                    "Set to false for routine notifications (e.g., sales, surveys)."
                ),
            },
        },
        "required": ["message", "important"],
    },
}

fetch_history_declaration = {
    "name": "fetch_conversation_history",
    "description": (
        "Fetch previous conversation histories for this specific camera. "
        "Use this if you need context on past interactions with visitors at this door."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
    },
}

class GeminiAgent:
    def __init__(self, api_key: str, model: str, prompt_config: dict, ha_client, camera_name: str):
        self.client = genai.Client(api_key=api_key)
        self.model = model
        self.ha_client = ha_client
        self.camera_name = camera_name
        self.shutdown_requested = asyncio.Event()
        self._session_resumption_handle = None
        self._initial_message_sent = False
        self._reconnect_backoff_seconds = 0.5
        self._max_reconnect_backoff_seconds = 8.0

        # Build System Instruction
        identity = prompt_config.get("identity", "")
        general_inst = prompt_config.get("general_instructions", "")
        guidelines = prompt_config.get("guidelines", "")
        language = prompt_config.get("language_spoken", "")

        self.system_instruction = f"{identity}\n\nYour Responsibilities:\n{general_inst}\n\nGuidelines:\n{guidelines}\n\n{language}"

        self.tools = [{"function_declarations": [
            final_response_declaration, 
            notify_owner_declaration,
            fetch_history_declaration
        ]}]

        self.config = types.LiveConnectConfig(
            response_modalities=[types.Modality.AUDIO],
            system_instruction=types.Content(parts=[types.Part(text=self.system_instruction)]),
            tools=self.tools,
        )

    @staticmethod
    def _extract_value(container, *names):
        for name in names:
            if isinstance(container, dict) and name in container:
                return container.get(name)
            if hasattr(container, name):
                return getattr(container, name)
        return None

    def _extract_session_resumption_handle(self, response):
        update = self._extract_value(response, "session_resumption_update", "sessionResumptionUpdate")
        if update is None:
            return None
        resumable = self._extract_value(update, "resumable")
        new_handle = self._extract_value(update, "new_handle", "newHandle")
        if resumable and new_handle:
            return new_handle
        return None

    def _extract_go_away_time_left(self, response):
        go_away = self._extract_value(response, "go_away", "goAway")
        if not go_away:
            return None
        return self._extract_value(go_away, "time_left", "timeLeft")

    def _build_connect_config(self):
        session_resumption = {}
        if self._session_resumption_handle:
            session_resumption["handle"] = self._session_resumption_handle
        # Always include this so the server emits SessionResumptionUpdate tokens.
        return types.LiveConnectConfig(
            response_modalities=self.config.response_modalities,
            system_instruction=self.config.system_instruction,
            tools=self.config.tools,
            session_resumption=session_resumption,
        )

    def _build_audio_blob(self, msg) -> types.Blob:
        """Normalize mic chunks into an explicit PCM blob for Live API input."""
        if isinstance(msg, dict):
            data = msg.get("data", b"")
            mime_type = msg.get("mime_type") or "audio/pcm"
        elif isinstance(msg, (bytes, bytearray)):
            data = bytes(msg)
            mime_type = "audio/pcm"
        else:
            raise ValueError(f"Unsupported mic payload type: {type(msg).__name__}")

        if isinstance(data, bytearray):
            data = bytes(data)
        if not isinstance(data, bytes):
            raise ValueError("Mic payload data must be bytes.")

        if mime_type.startswith("audio/pcm") and "rate=" not in mime_type:
            mime_type = f"audio/pcm;rate={GEMINI_INPUT_SAMPLE_RATE}"

        return types.Blob(data=data, mime_type=mime_type)

    async def _handle_tool_call(self, session, tool_call):
        function_responses = []
        should_close_session = False

        for fc in tool_call.function_calls:
            args = fc.args if isinstance(fc.args, dict) else {}
            logger.info(f"Tool called: {fc.name} args={args}")
            try:
                if fc.name == "final_response":
                    summary = args.get("summary", "")
                    await self.ha_client.add_interaction_todo(self.camera_name, summary)
                    logger.info(
                        "Model requested session finish via final_response for camera=%s summary=%s",
                        self.camera_name,
                        summary,
                    )
                    result = {"status": "logged", "summary": summary}
                    should_close_session = True
                elif fc.name == "notify_owner":
                    message = args.get("message", "")
                    important = args.get("important", False)
                    await self.ha_client.set_notification_content(message)
                    logger.info(f"NOTIFY OWNER: {message} (Important: {important})")
                    result = {"status": "sent", "message": message}
                elif fc.name == "fetch_conversation_history":
                    history = await self.ha_client.fetch_conversation_history(self.camera_name)
                    result = {"history": history}
                else:
                    result = {"error": f"Unknown function: {fc.name}"}
            except Exception as e:
                result = {"error": f"Tool execution failed for {fc.name}", "details": str(e)}

            function_responses.append(
                types.FunctionResponse(id=fc.id, name=fc.name, response=result)
            )

        await session.send_tool_response(function_responses=function_responses)

        if should_close_session:
            logger.info("Setting Gemini shutdown_requested after final_response")
            self.shutdown_requested.set()

    async def run(self, mic_queue: asyncio.Queue, speaker_queue: asyncio.Queue, speaker_track, home_status: str):
        while not self.shutdown_requested.is_set():
            reconnect_requested = asyncio.Event()

            try:
                async with self.client.aio.live.connect(
                    model=self.model,
                    config=self._build_connect_config(),
                ) as session:
                    logger.info("Connected to Gemini Live API")
                    self._reconnect_backoff_seconds = 0.5

                    if not self._initial_message_sent:
                        initial_message = (
                            f"Someone rang the doorbell. The homeowner is currently {home_status}. "
                            "Start the conversation politely and immediately."
                        )
                        await session.send_realtime_input(text=initial_message)
                        self._initial_message_sent = True

                    async def send_audio():
                        while not self.shutdown_requested.is_set() and not reconnect_requested.is_set():
                            try:
                                msg = await asyncio.wait_for(mic_queue.get(), timeout=0.3)
                            except asyncio.TimeoutError:
                                continue

                            try:
                                # HALF-DUPLEX AEC Alternative: don't send mic audio if model is speaking
                                if speaker_track and getattr(speaker_track, "is_speaking", False):
                                    continue
                                await session.send_realtime_input(audio=self._build_audio_blob(msg))
                            except Exception as e:
                                logger.warning(f"send_realtime_input failed: {e}")
                                reconnect_requested.set()
                                break

                    async def receive_audio():
                        while not self.shutdown_requested.is_set() and not reconnect_requested.is_set():
                            try:
                                turn = session.receive()
                                async for response in turn:
                                    new_handle = self._extract_session_resumption_handle(response)
                                    if new_handle and new_handle != self._session_resumption_handle:
                                        self._session_resumption_handle = new_handle
                                        logger.debug("Updated Gemini Live session resumption handle")

                                    time_left = self._extract_go_away_time_left(response)
                                    if time_left is not None:
                                        logger.warning(
                                            f"Gemini Live sent goAway (time_left={time_left}), reconnecting"
                                        )
                                        reconnect_requested.set()
                                        break

                                    if response.tool_call:
                                        await self._handle_tool_call(session, response.tool_call)
                                        continue

                                    if response.server_content and response.server_content.model_turn:
                                        for part in response.server_content.model_turn.parts:
                                            if part.inline_data and isinstance(part.inline_data.data, bytes):
                                                if speaker_queue.full():
                                                    try:
                                                        speaker_queue.get_nowait()
                                                    except asyncio.QueueEmpty:
                                                        pass
                                                await speaker_queue.put(part.inline_data.data)
                                if reconnect_requested.is_set():
                                    break
                            except Exception as e:
                                logger.warning(f"receive_model_audio error: {e}")
                                reconnect_requested.set()
                                break

                    async with asyncio.TaskGroup() as tg:
                        tg.create_task(send_audio())
                        tg.create_task(receive_audio())

            except Exception as e:
                logger.warning(f"Gemini Live connection failed: {e}")
                reconnect_requested.set()

            if self.shutdown_requested.is_set():
                break

            if reconnect_requested.is_set():
                delay = self._reconnect_backoff_seconds
                logger.info(f"Reconnecting Gemini Live in {delay:.1f}s")
                await asyncio.sleep(delay)
                self._reconnect_backoff_seconds = min(
                    self._reconnect_backoff_seconds * 2,
                    self._max_reconnect_backoff_seconds,
                )
