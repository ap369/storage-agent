from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketDisconnect

from agent.core import run_conversation
from auth import verify_token
from storage.db import create_conversation, get_conversation_messages, insert_message

router = APIRouter()


@router.websocket("/ws/chat")
async def chat_websocket(websocket: WebSocket) -> None:
    await websocket.accept()

    settings = websocket.app.state.settings
    first_frame = await websocket.receive_json()
    if not verify_token(first_frame.get("token"), settings.API_TOKEN):
        await websocket.send_json({"type": "error", "message": "unauthorized"})
        await websocket.close(code=4401)
        return

    db = websocket.app.state.db
    registry = websocket.app.state.registry
    system_prompt = websocket.app.state.system_prompt
    llm_client = websocket.app.state.llm_client

    async def on_event(event: dict) -> None:
        if event["type"] != "final":
            await websocket.send_json(event)

    try:
        while True:
            frame = await websocket.receive_json()
            if frame.get("type") != "message":
                continue

            conversation_id = frame.get("conversation_id")
            if conversation_id is None:
                conversation_id = await create_conversation(db, source="webview")

            await insert_message(db, conversation_id, role="user", content=frame.get("content", ""))
            history = await get_conversation_messages(db, conversation_id)

            try:
                result = await run_conversation(
                    llm_client,
                    registry,
                    system_prompt,
                    history,
                    max_turns=settings.MAX_TOOL_TURNS,
                    on_event=on_event,
                )
            except Exception as exc:  # an LLM/tool-loop failure must not crash the connection
                await websocket.send_json({"type": "error", "message": str(exc)})
                continue

            await insert_message(db, conversation_id, role="assistant", content=result)
            await websocket.send_json(
                {"type": "final", "content": result, "conversation_id": conversation_id}
            )
    except WebSocketDisconnect:
        return
