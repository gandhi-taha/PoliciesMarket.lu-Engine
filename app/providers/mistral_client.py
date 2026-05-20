from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.config import settings

CHAT_COMPLETIONS_PATH = "/v1/chat/completions"


@dataclass
class ToolCall:
    call_id: str | None
    name: str
    arguments: dict[str, Any]


@dataclass
class ChatResponse:
    content: str | None
    tool_calls: list[ToolCall]
    message: dict[str, Any]
    raw: dict[str, Any]


class MistralClientProtocol(Protocol):
    model: str

    def chat(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        parallel_tool_calls: bool = False,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ChatResponse: ...


class MistralClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.model = model
        self._base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            timeout=timeout_seconds,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    @classmethod
    def from_settings(cls) -> "MistralClient":
        if not settings.mistral_api_key:
            raise ValueError("MISTRAL_API_KEY is not configured")
        return cls(
            api_key=settings.mistral_api_key,
            model=settings.mistral_model,
            base_url=settings.mistral_base_url,
            timeout_seconds=settings.mistral_timeout_seconds,
        )

    def chat(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        parallel_tool_calls: bool = False,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ChatResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "parallel_tool_calls": parallel_tool_calls,
        }
        if tools is not None:
            payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        response = self._client.post(f"{self._base_url}{CHAT_COMPLETIONS_PATH}", json=payload)
        response.raise_for_status()
        data = response.json()

        choices = data.get("choices", [])
        if not choices:
            raise ValueError("Mistral response missing choices")
        message = choices[0].get("message", {})

        tool_calls_raw = message.get("tool_calls", []) or []
        tool_calls: list[ToolCall] = []
        for tool_call in tool_calls_raw:
            function = tool_call.get("function", {})
            name = function.get("name")
            if not name:
                raise ValueError("Tool call missing name")
            arguments = function.get("arguments", {})
            if isinstance(arguments, str):
                arguments = json.loads(arguments) if arguments else {}
            if not isinstance(arguments, dict):
                raise ValueError("Tool call arguments must be an object")
            tool_calls.append(
                ToolCall(call_id=tool_call.get("id"), name=name, arguments=arguments)
            )

        return ChatResponse(
            content=message.get("content"),
            tool_calls=tool_calls,
            message=message,
            raw=data,
        )
