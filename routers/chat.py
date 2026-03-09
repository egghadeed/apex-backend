# apex_backend/routers/chat.py
# Proxies Claude and OpenAI requests — user's token in, streamed response out.

import anthropic
import json
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Any, Optional
from auth_utils import require_active_subscription
from database import log_usage
from config import (
    ANTHROPIC_API_KEY, OPENAI_API_KEY,
    TIER_DEFAULT_MODEL, TIER_SELECTABLE_MODELS, VISION_CAPABLE,
)

router = APIRouter()

SYSTEM_PROMPT = (
    "You are Apex, a helpful desktop AI assistant. "
    "You can see screenshots and read text the user highlights. "
    "Be concise but thorough when directed. "
    "In general, answer questions with minimal working."
)

# o1/o3 models use max_completion_tokens and don't accept a system role param
O1_MODELS = {"o1-mini", "o1", "o3-mini", "o3"}


class Message(BaseModel):
    role: str
    content: str | list[dict[str, Any]]


class ChatRequest(BaseModel):
    messages: list[Message]
    model: Optional[str] = None   # client may request a specific model


def _resolve_model(requested: str | None, tier: str) -> str:
    """Return the model to use, validated against the tier's allowed list."""
    allowed = TIER_SELECTABLE_MODELS.get(tier, [TIER_DEFAULT_MODEL.get(tier, "gpt-4o-mini")])
    if requested and requested in allowed:
        return requested
    return TIER_DEFAULT_MODEL.get(tier, "gpt-4o-mini")


def _is_openai(model: str) -> bool:
    return model.startswith("gpt-") or model.startswith("o1") or model.startswith("o3")


def _convert_messages_for_openai(messages: list[dict], model: str) -> list[dict]:
    """Convert Anthropic-style message format to OpenAI format."""
    vision = VISION_CAPABLE.get(model, True)
    converted = []

    # o1/o3: inject system as first user message
    if model in O1_MODELS:
        converted.append({"role": "user", "content": f"[System]: {SYSTEM_PROMPT}"})

    for msg in messages:
        role = msg["role"]
        content = msg["content"]

        if isinstance(content, str):
            converted.append({"role": role, "content": content})
            continue

        # List of content blocks — convert image blocks
        parts = []
        for block in content:
            btype = block.get("type")
            if btype == "text":
                parts.append({"type": "text", "text": block.get("text", "")})
            elif btype == "image":
                if not vision:
                    # Non-vision model: replace image with placeholder text
                    parts.append({"type": "text",
                                  "text": "[screenshot — not supported by this model]"})
                else:
                    src = block.get("source", {})
                    b64  = src.get("data", "")
                    mime = src.get("media_type", "image/png")
                    parts.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64}"},
                    })
        converted.append({"role": role, "content": parts})

    return converted


def _stream_openai(model: str, messages: list[dict]):
    """Yield SSE chunks from OpenAI streaming API."""
    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_API_KEY)

    oai_messages = _convert_messages_for_openai(messages, model)

    kwargs: dict = {
        "model":    model,
        "messages": oai_messages,
        "stream":   True,
    }
    if model in O1_MODELS:
        kwargs["max_completion_tokens"] = 2048
    else:
        kwargs["max_tokens"] = 2048
        kwargs["messages"]   = [{"role": "system", "content": SYSTEM_PROMPT}] + oai_messages

    input_tokens = output_tokens = 0

    try:
        stream = client.chat.completions.create(**kwargs)
        for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                yield f"data: {json.dumps({'type': 'chunk', 'text': delta.content})}\n\n"
            # Capture usage from last chunk
            if hasattr(chunk, "usage") and chunk.usage:
                input_tokens  = chunk.usage.prompt_tokens or 0
                output_tokens = chunk.usage.completion_tokens or 0
        yield f"data: {json.dumps({'type': 'done', 'input_tokens': input_tokens, 'output_tokens': output_tokens})}\n\n"
    except Exception as e:
        yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"


def _stream_anthropic(model: str, messages: list[dict]):
    """Yield SSE chunks from Anthropic streaming API."""
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    input_tokens = output_tokens = 0
    try:
        with client.messages.stream(
            model=model,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            messages=messages,
        ) as stream:
            for text in stream.text_stream:
                yield f"data: {json.dumps({'type': 'chunk', 'text': text})}\n\n"
            usage = stream.get_final_message().usage
            input_tokens  = usage.input_tokens
            output_tokens = usage.output_tokens
        yield f"data: {json.dumps({'type': 'done', 'input_tokens': input_tokens, 'output_tokens': output_tokens})}\n\n"
    except anthropic.APIError as e:
        yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
    except Exception:
        yield f"data: {json.dumps({'type': 'error', 'message': 'Server error'})}\n\n"


@router.post("/stream")
def stream_chat(
    body: ChatRequest,
    user: dict = Depends(require_active_subscription),
):
    tier  = user.get("tier", "free")
    model = _resolve_model(body.model, tier)

    raw_messages = [m.model_dump() for m in body.messages]

    def generate():
        if _is_openai(model):
            gen = _stream_openai(model, raw_messages)
        else:
            gen = _stream_anthropic(model, raw_messages)

        input_tokens = output_tokens = 0
        for chunk in gen:
            yield chunk
            # Extract token counts from the done event
            if '"type": "done"' in chunk:
                try:
                    data = json.loads(chunk.split("data: ", 1)[1])
                    input_tokens  = data.get("input_tokens", 0)
                    output_tokens = data.get("output_tokens", 0)
                except Exception:
                    pass

        try:
            log_usage(user["id"], input_tokens, output_tokens, model)
        except Exception:
            pass

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":               "no-cache",
            "X-Accel-Buffering":           "no",
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
        "used":      used,
        "limit":     limit,
        "unlimited": limit == -1,
        "tier":      user["tier"],
    }
