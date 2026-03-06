# apex_backend/routers/chat.py
# Proxies Claude requests — user's token in, streamed response out.
# Users never touch Anthropic API keys.

import anthropic
import json
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Any
from auth_utils import require_active_subscription
from database import log_usage
from config import ANTHROPIC_API_KEY, TIER_MODELS

router = APIRouter()

SYSTEM_PROMPT = (
    "You are Apex, a helpful desktop AI assistant. "
    "You can see screenshots and read text the user highlights. "
    "Be concise but thorough when directed. "
    "In general, answer questions with minimal working."
)

class MessageContent(BaseModel):
    type: str
    text: str | None = None
    source: dict | None = None   # for image blocks

class Message(BaseModel):
    role: str
    content: str | list[dict[str, Any]]

class ChatRequest(BaseModel):
    messages: list[Message]


@router.post("/stream")
def stream_chat(
    body: ChatRequest,
    user: dict = Depends(require_active_subscription),
):
    tier  = user.get("tier", "free")
    model = TIER_MODELS.get(tier, "claude-haiku-4-5-20251001")

    # Convert pydantic messages to raw dicts for Anthropic SDK
    raw_messages = [m.model_dump() for m in body.messages]

    def generate():
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        input_tokens  = 0
        output_tokens = 0
        try:
            with client.messages.stream(
                model=model,
                max_tokens=2048,
                system=SYSTEM_PROMPT,
                messages=raw_messages,
            ) as stream:
                for text in stream.text_stream:
                    # Send each chunk as SSE
                    yield f"data: {json.dumps({'type': 'chunk', 'text': text})}\n\n"

                # Usage stats after stream completes
                usage = stream.get_final_message().usage
                input_tokens  = usage.input_tokens
                output_tokens = usage.output_tokens

            # Log usage (non-blocking — errors silently swallowed)
            try:
                log_usage(user["id"], input_tokens, output_tokens, model)
            except Exception:
                pass

            yield f"data: {json.dumps({'type': 'done'})}\n\n"

        except anthropic.APIError as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': 'Server error'})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":               "no-cache",
            "X-Accel-Buffering":           "no",   # disable Nginx buffering
            "Access-Control-Allow-Origin": "*",
        },
    )


@router.get("/usage")
def get_usage(user: dict = Depends(require_active_subscription)):
    from database import get_usage_this_month
    from config import TIER_LIMITS
    used  = get_usage_this_month(user["id"])
    limit = TIER_LIMITS.get(user["tier"], 50)
    return {
        "used":       used,
        "limit":      limit,
        "unlimited":  limit == -1,
        "tier":       user["tier"],
    }
