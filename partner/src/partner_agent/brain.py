"""Model adapter used by Partner and Human orchestration flows."""

from __future__ import annotations

from typing import Any

import httpx

from .settings import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL, DEEPSEEK_TIMEOUT_SECONDS


class DeepSeekChatBrain:
    def __init__(self) -> None:
        """Load model connection settings from runtime configuration."""
        self._base_url = DEEPSEEK_BASE_URL.rstrip("/")
        self._api_key = DEEPSEEK_API_KEY
        self._model = DEEPSEEK_MODEL
        self._timeout = DEEPSEEK_TIMEOUT_SECONDS

    @property
    def enabled(self) -> bool:
        """Return whether model configuration is present and non-empty."""
        return bool(self._base_url and self._api_key and self._model)

    async def chat(
        self,
        user_text: str,
        history: list[dict[str, str]] | None = None,
        system_prompt: str | None = None,
        timeout_seconds: float | None = None,
    ) -> str:
        """Execute one chat completion request and return plain text content."""
        if not self.enabled:
            raise RuntimeError("DeepSeek brain is not configured")

        messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": system_prompt
                or "你是一个文本咨询智能体，仅输出简洁清晰的中文文本回答。",
            }
        ]
        for message in history or []:
            role = message.get("role", "")
            content = (message.get("content") or "").strip()
            if role in {"user", "assistant"} and content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": user_text})

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": 0.3,
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self._base_url}/chat/completions"

        timeout = timeout_seconds if timeout_seconds is not None else self._timeout
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("DeepSeek response missing choices")
        message = (choices[0] or {}).get("message") or {}
        content = (message.get("content") or "").strip()
        if not content:
            raise RuntimeError("DeepSeek response content is empty")
        return content

