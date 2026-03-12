from __future__ import annotations

from typing import Any
import time

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

    def _log_call(
        self,
        *,
        alias: str,
        provider_name: str,
        model: str,
        success: bool,
        latency_ms: int,
        error_message: str | None = None,
    ) -> None:
        from ..database import SessionLocal
        from ..models import ModelCallLog

        session = SessionLocal()
        try:
            session.add(ModelCallLog(
                alias=alias,
                provider_name=provider_name,
                model=model,
                success=success,
                latency_ms=latency_ms,
                error_message=error_message[:255] if error_message else None,
            ))
            session.commit()
        except Exception:
            session.rollback()
        finally:
            session.close()

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
        start = time.perf_counter()
        try:
            result = self._openai_provider.complete_json(profile, system_prompt, user_prompt)
            latency_ms = int((time.perf_counter() - start) * 1000)
            self._log_call(
                alias=profile.alias,
                provider_name=provider_name,
                model=profile.model,
                success=True,
                latency_ms=latency_ms,
            )
            return result, provider_name
        except Exception as exc:
            latency_ms = int((time.perf_counter() - start) * 1000)
            self._log_call(
                alias=profile.alias,
                provider_name=provider_name,
                model=profile.model,
                success=False,
                latency_ms=latency_ms,
                error_message=str(exc),
            )
            raise

    def complete_text(self, alias: str, system_prompt: str, user_prompt: str) -> tuple[str | None, str]:
        profile = self.resolve(alias)
        if not profile or not profile.enabled:
            return None, "heuristic"
        provider_name = profile.provider_name
        start = time.perf_counter()
        try:
            result = self._openai_provider.complete_text(profile, system_prompt, user_prompt)
            latency_ms = int((time.perf_counter() - start) * 1000)
            self._log_call(
                alias=profile.alias,
                provider_name=provider_name,
                model=profile.model,
                success=True,
                latency_ms=latency_ms,
            )
            return result, provider_name
        except Exception as exc:
            latency_ms = int((time.perf_counter() - start) * 1000)
            self._log_call(
                alias=profile.alias,
                provider_name=provider_name,
                model=profile.model,
                success=False,
                latency_ms=latency_ms,
                error_message=str(exc),
            )
            raise


model_router = ModelRouter()
