from __future__ import annotations

from typing import Any

from ..model_catalog import DEFAULT_MODEL_PROFILES, ModelProfile
from ..providers.llm.openai_compatible import OpenAICompatibleProvider


class ModelRouter:
    def __init__(self) -> None:
        self._profiles = {profile.alias: profile for profile in DEFAULT_MODEL_PROFILES}
        self._openai_provider = OpenAICompatibleProvider()

    def _load_db_profiles(self) -> dict[str, ModelProfile]:
        from ..database import SessionLocal
        from ..models import ModelConfig

        session = SessionLocal()
        try:
            configs = session.query(ModelConfig).all()
        finally:
            session.close()
        profiles: dict[str, ModelProfile] = {}
        for config in configs:
            profiles[config.alias] = ModelProfile(
                alias=config.alias,
                provider_kind=config.provider_kind,
                provider_name=config.provider_name,
                model=config.model,
                base_url=config.base_url,
                api_key=config.api_key,
                temperature=float(config.temperature),
                max_tokens=int(config.max_tokens),
                enabled=bool(config.enabled) and bool(config.api_key),
                description=config.description or "Custom profile",
            )
        return profiles

    def list_profiles(self) -> list[ModelProfile]:
        profiles = dict(self._profiles)
        profiles.update(self._load_db_profiles())
        return list(profiles.values())

    def resolve(self, alias: str) -> ModelProfile | None:
        db_profiles = self._load_db_profiles()
        return db_profiles.get(alias) or self._profiles.get(alias)

    def complete_json(self, alias: str, system_prompt: str, user_prompt: str) -> tuple[dict[str, Any] | None, str]:
        profile = self.resolve(alias)
        if not profile or not profile.enabled:
            return None, "heuristic"
        provider_name = profile.provider_name
        result = self._openai_provider.complete_json(profile, system_prompt, user_prompt)
        return result, provider_name

    def complete_text(self, alias: str, system_prompt: str, user_prompt: str) -> tuple[str | None, str]:
        profile = self.resolve(alias)
        if not profile or not profile.enabled:
            return None, "heuristic"
        provider_name = profile.provider_name
        result = self._openai_provider.complete_text(profile, system_prompt, user_prompt)
        return result, provider_name


model_router = ModelRouter()
