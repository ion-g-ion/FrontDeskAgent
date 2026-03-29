import asyncio
import logging
from google import genai
from google.genai import types

logger = logging.getLogger("llm")

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

        self.config = {
            "response_modalities": ["AUDIO"],
            "system_instruction": self.system_instruction,
            "tools": self.tools,
        }

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
            self.shutdown_requested.set()

    async def run(self, mic_queue: asyncio.Queue, speaker_queue: asyncio.Queue, speaker_track, home_status: str):
        async with self.client.aio.live.connect(model=self.model, config=self.config) as session:
            initial_message = (
                f"Someone rang the doorbell. The homeowner is currently {home_status}. "
                "Start the conversation politely and immediately."
            )
            await session.send_client_content(
                turns={"parts": [{"text": initial_message}]}
            )

            async def send_audio():
                while not self.shutdown_requested.is_set():
                    try:
                        msg = await asyncio.wait_for(mic_queue.get(), timeout=0.3)
                    except asyncio.TimeoutError:
                        continue

                    try:
                        # HALF-DUPLEX AEC Alternative: don't send mic audio if model is speaking
                        if speaker_track and getattr(speaker_track, 'is_speaking', False):
                            continue
                        await session.send_realtime_input(audio=msg)
                    except Exception as e:
                        logger.warning(f"send_realtime_input failed: {e}")
                        await asyncio.sleep(0.2)

            async def receive_audio():
                while not self.shutdown_requested.is_set():
                    try:
                        turn = session.receive()
                        async for response in turn:
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
                    except Exception as e:
                        logger.warning(f"receive_model_audio error: {e}")
                        await asyncio.sleep(0.2)

            try:
                async with asyncio.TaskGroup() as tg:
                    tg.create_task(send_audio())
                    tg.create_task(receive_audio())
            finally:
                self.shutdown_requested.set()
                close_fn = getattr(session, "close", None)
                if callable(close_fn):
                    maybe_coro = close_fn()
                    if asyncio.iscoroutine(maybe_coro):
                        await maybe_coro
