"""LLM-backed response generation for Partner agent."""

from __future__ import annotations

from typing import Any

import httpx

from .settings import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL, DEEPSEEK_TIMEOUT_SECONDS


class DeepSeekChatBrain:
    def __init__(self) -> None:
        self._base_url = DEEPSEEK_BASE_URL.rstrip("/")
        self._api_key = DEEPSEEK_API_KEY
        self._model = DEEPSEEK_MODEL
        self._timeout = DEEPSEEK_TIMEOUT_SECONDS

    @property
    def enabled(self) -> bool:
        return bool(self._base_url and self._api_key and self._model)

    async def chat(self, user_text: str) -> str:
        if not self.enabled:
            raise RuntimeError("DeepSeek brain is not configured")

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {
                    "role": "system",
                    "content": "你是一个文本咨询智能体，仅输出简洁清晰的中文文本回答。",
                },
                {"role": "user", "content": user_text},
            ],
            "temperature": 0.3,
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self._base_url}/chat/completions"

        async with httpx.AsyncClient(timeout=self._timeout) as client:
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

