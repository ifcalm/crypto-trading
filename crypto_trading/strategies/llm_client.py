"""DeepSeek API wrapper for trading decisions (OpenAI-compatible)."""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

DEEPSEEK_BASE = "https://api.deepseek.com"


class LLMClient:
    """Lightweight wrapper around DeepSeek API for structured JSON output.

    Usage::

        client = LLMClient(model="deepseek-chat")
        result = await client.analyze(
            system_prompt="You are a crypto trading analyst...",
            user_prompt="Here is the orderbook data...",
            temperature=0.3,
            max_tokens=512,
        )
        # result is a parsed JSON dict
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "deepseek-chat",
        base_url: str | None = None,
    ):
        key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        if not key:
            raise ValueError(
                "DEEPSEEK_API_KEY not set. Set the environment variable or pass api_key."
            )
        self._client = httpx.AsyncClient(
            base_url=base_url or DEEPSEEK_BASE,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )
        self.model = model

    async def analyze(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.3,
        max_tokens: int = 512,
    ) -> dict[str, Any]:
        """Send a prompt and return parsed JSON response."""
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        resp = await self._client.post(
            "/chat/completions",
            json={
                "model": self.model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
        )
        resp.raise_for_status()
        data = resp.json()

        text = data["choices"][0]["message"]["content"]

        return self._parse_json(text)

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any]:
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [ln for ln in lines if not ln.startswith("```")]
            text = "\n".join(lines)

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end > start:
                return json.loads(text[start : end + 1])
            raise

    async def close(self) -> None:
        await self._client.aclose()
