"""
backend/tests/mock_vllm_server.py
==================================
OpenAI-compatible vLLM testing service container.
Serves /health, /v1/models, and /v1/chat/completions for local cluster validation.
"""

from typing import Any
from fastapi import FastAPI
import uvicorn

app = FastAPI(title="Local vLLM Testing Inference Engine")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "healthy", "engine": "vLLM-compatible"}


@app.get("/v1/models")
def list_models() -> dict[str, Any]:
    return {
        "object": "list",
        "data": [
            {
                "id": "meta-llama/Meta-Llama-3.1-8B-Instruct",
                "object": "model",
                "created": 1700000000,
                "owned_by": "vllm",
            }
        ],
    }


@app.post("/v1/chat/completions")
def chat_completions(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "chatcmpl-mock-vllm",
        "object": "chat.completion",
        "created": 1700000000,
        "model": payload.get("model", "meta-llama/Meta-Llama-3.1-8B-Instruct"),
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": (
                        '{"statutory_sections": ["Section 27, Indian Contract Act, 1872"], '
                        '"case_citations": ["AIR 1967 SC 1098"], '
                        '"findings_summary": "Agreements in restraint of trade are void under Indian Law.", '
                        '"confidence": "HIGH"}'
                    ),
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 150,
            "completion_tokens": 45,
            "total_tokens": 195,
        },
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)

