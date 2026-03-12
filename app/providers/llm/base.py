from __future__ import annotations

from typing import Any, Protocol

from ...model_catalog import ModelProfile


class LlmProvider(Protocol):
    def complete_json(self, profile: ModelProfile, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        ...

    def complete_text(self, profile: ModelProfile, system_prompt: str, user_prompt: str) -> str:
        ...
