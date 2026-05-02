"""AcuBuddy server — OpenAI-compatible chat endpoint backed by RAG over Acumatica docs.

Start with:
    uvicorn server:app --host 127.0.0.1 --port 5000 --reload

Endpoints:
    POST /v1/chat/completions   OpenAI-compatible (streaming + non-streaming)
    GET  /v1/models             List available models
    GET  /health                Health check
"""

import json
import os
import time
import uuid

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, JSONResponse

from acu_buddy.rag import load_index, search

load_dotenv()

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
INDEX_DIR = os.path.join(os.path.dirname(__file__), "chroma_db")
SEARCH_K = int(os.getenv("ACUBUDDY_SEARCH_K", "5"))
MODEL_ID = "acubuddy-deepseek-v4"

SYSTEM_PROMPT = (
    "You are an Acumatica ERP development assistant. "
    "Use ONLY the provided context from Acumatica documentation to answer the user's question. "
    "If the context does not contain enough information, say you don't know and suggest the user consult "
    "the official Acumatica documentation. "
    "Include relevant code examples when available. "
    "Be concise and accurate."
)

app = FastAPI(title="AcuBuddy", version="1.0.0")
_vecstore = None


def _get_vecstore():
    global _vecstore
    if _vecstore is None:
        if not os.path.isdir(INDEX_DIR):
            raise RuntimeError(
                "Vector index not found. Run 'python build_index.py' first."
            )
        _vecstore = load_index(INDEX_DIR)
    return _vecstore


def _build_context(query: str) -> str:
    vecstore = _get_vecstore()
    chunks = search(vecstore, query, k=SEARCH_K)
    if not chunks:
        return ""
    return "\n\n---\n\n".join(
        f"[Source {i + 1}]\n{chunk}" for i, chunk in enumerate(chunks)
    )


def _inject_context(messages: list[dict]) -> tuple[list[dict], str]:
    user_query = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            user_query = msg.get("content", "")
            break

    context = ""
    try:
        context = _build_context(user_query)
    except RuntimeError:
        pass

    system_content = SYSTEM_PROMPT
    if context:
        system_content += f"\n\nRelevant Acumatica documentation:\n\n{context}"

    api_messages = [{"role": "system", "content": system_content}]
    if context:
        api_messages.append({
            "role": "system",
            "content": "Use the documentation excerpts above to answer the user's question accurately.",
        })
    api_messages.extend(messages)

    return api_messages, user_query


async def _stream_deepseek(api_messages: list[dict], temperature: float, model_id: str):
    if not DEEPSEEK_API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY not set. Create a .env file.")

    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "deepseek-chat",
        "messages": api_messages,
        "temperature": temperature,
        "max_tokens": 8192,
        "stream": True,
    }

    timeout = httpx.Timeout(120.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream(
            "POST",
            f"{DEEPSEEK_BASE_URL}/chat/completions",
            json=payload,
            headers=headers,
        ) as resp:
            resp.raise_for_status()
            chunk_id = f"chatcmpl-{uuid.uuid4().hex[:29]}"
            created = int(time.time())

            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str.strip() == "[DONE]":
                    yield "data: [DONE]\n\n"
                    break
                try:
                    upstream = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                upstream_choices = upstream.get("choices", [])
                delta = upstream_choices[0].get("delta", {}) if upstream_choices else {}
                finish_reason = (
                    upstream_choices[0].get("finish_reason", None)
                    if upstream_choices
                    else None
                )

                chunk = {
                    "id": chunk_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model_id,
                    "choices": [
                        {
                            "index": 0,
                            "delta": delta,
                            "finish_reason": finish_reason,
                        }
                    ],
                }
                yield f"data: {json.dumps(chunk)}\n\n"


async def _call_deepseek_async(api_messages: list[dict], temperature: float) -> dict:
    if not DEEPSEEK_API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY not set. Create a .env file.")

    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "deepseek-chat",
        "messages": api_messages,
        "temperature": temperature,
        "max_tokens": 8192,
    }

    timeout = httpx.Timeout(120.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{DEEPSEEK_BASE_URL}/chat/completions",
            json=payload,
            headers=headers,
        )
        resp.raise_for_status()
        return resp.json()


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/v1/models")
async def list_models():
    return {
        "object": "list",
        "data": [
            {
                "id": MODEL_ID,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "acubuddy",
            }
        ],
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    data = await request.json()
    if not data or "messages" not in data:
        return JSONResponse({"error": "messages field is required"}, status_code=400)

    messages = data.get("messages", [])
    temperature = float(data.get("temperature", 0.7))
    do_stream = data.get("stream", False)

    api_messages, user_query = _inject_context(messages)

    if not user_query:
        return JSONResponse({"error": "No user message found"}, status_code=400)

    if do_stream:
        async def event_stream():
            try:
                async for chunk in _stream_deepseek(api_messages, temperature, MODEL_ID):
                    yield chunk
            except Exception as e:
                error_payload = json.dumps({
                    "error": f"DeepSeek API error: {str(e)}"
                })
                yield f"data: {error_payload}\n\n"
                yield "data: [DONE]\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )

    try:
        deepseek_resp = await _call_deepseek_async(api_messages, temperature)
    except Exception as e:
        return JSONResponse(
            {"error": f"DeepSeek API error: {str(e)}"}, status_code=502
        )

    choice = deepseek_resp["choices"][0]
    usage = deepseek_resp.get("usage", {})

    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:29]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": MODEL_ID,
        "choices": [
            {
                "index": 0,
                "message": choice["message"],
                "finish_reason": choice.get("finish_reason", "stop"),
            }
        ],
        "usage": {
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
        },
    }
