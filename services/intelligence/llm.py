"""Small, optional Responses-compatible LLM client.

The product remains useful without a model: callers must provide a
deterministic fallback. Keeping the transport here also makes prompts and
data egress explicit and testable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class LlmConfig:
    api_key: str
    model: str
    endpoint: str

    @classmethod
    def from_env(cls) -> LlmConfig | None:
        api_key = os.getenv("PROCINTEL_LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
        model = os.getenv("PROCINTEL_LLM_MODEL") or os.getenv("OPENAI_MODEL")
        endpoint = os.getenv("PROCINTEL_LLM_ENDPOINT")
        if not endpoint and os.getenv("OPENAI_API_KEY"):
            endpoint = "https://api.openai.com/v1/responses"
        if not api_key or not model or not endpoint:
            return None
        return cls(api_key=api_key, model=model, endpoint=endpoint)


def response_text(payload: dict[str, Any]) -> str:
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    fragments: list[str] = []
    for item in payload.get("output", []):
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if not isinstance(content, dict):
                continue
            text = content.get("text")
            if isinstance(text, str) and text.strip():
                fragments.append(text.strip())
    if fragments:
        return "\n".join(fragments)
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str) and content.strip():
            return content.strip()
    raise ValueError("LLM response did not contain text")


async def generate_text(
    http_client: httpx.AsyncClient,
    *,
    instructions: str,
    input_text: str,
    config: LlmConfig | None = None,
) -> str | None:
    config = config or LlmConfig.from_env()
    if config is None:
        return None
    response = await http_client.post(
        config.endpoint,
        headers={
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": config.model,
            "instructions": instructions,
            "input": input_text,
            "store": False,
        },
    )
    response.raise_for_status()
    return response_text(response.json())
