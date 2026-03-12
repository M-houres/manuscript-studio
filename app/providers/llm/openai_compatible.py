from __future__ import annotations

import json
from typing import Any

import httpx

from ...config import settings
from ...model_catalog import ModelProfile


class OpenAICompatibleProvider:
    def complete_json(self, profile: ModelProfile, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        payload = self._request(profile, system_prompt, user_prompt, expect_json=True)
        if isinstance(payload, dict):
            return payload
        return json.loads(payload)

    def complete_text(self, profile: ModelProfile, system_prompt: str, user_prompt: str) -> str:
        payload = self._request(profile, system_prompt, user_prompt, expect_json=False)
        return payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)

    def _request(self, profile: ModelProfile, system_prompt: str, user_prompt: str, expect_json: bool) -> dict[str, Any] | str:
        endpoint = profile.base_url.rstrip("/") + "/chat/completions"
        body: dict[str, Any] = {
            "model": profile.model,
            "temperature": profile.temperature,
            "max_tokens": profile.max_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if expect_json:
            body["response_format"] = {"type": "json_object"}

        with httpx.Client(timeout=settings.llm_timeout_seconds) as client:
            response = client.post(
                endpoint,
                json=body,
                headers={
                    "Authorization": f"Bearer {profile.api_key}",
                    "Content-Type": "application/json",
                },
            )
            response.raise_for_status()
            data = response.json()

        content = data["choices"][0]["message"]["content"]
        if expect_json:
            if isinstance(content, list):
                content = "".join(part.get("text", "") for part in content)
            return json.loads(content)
        if isinstance(content, list):
            return "".join(part.get("text", "") for part in content)
        return content
