from __future__ import annotations

from dataclasses import dataclass

from .config import settings


@dataclass(slots=True)
class ModelProfile:
    alias: str
    provider_kind: str
    provider_name: str
    model: str
    base_url: str
    api_key: str
    temperature: float
    max_tokens: int
    enabled: bool
    description: str


DEFAULT_MODEL_PROFILES = [
    ModelProfile(
        alias="review_fast",
        provider_kind="openai_compatible",
        provider_name="qwen",
        model=settings.qwen_review_model,
        base_url=settings.qwen_base_url,
        api_key=settings.qwen_api_key,
        temperature=0.2,
        max_tokens=1800,
        enabled=bool(settings.qwen_api_key),
        description="Qwen fast review lane",
    ),
    ModelProfile(
        alias="review_deep",
        provider_kind="openai_compatible",
        provider_name="deepseek",
        model=settings.deepseek_review_model,
        base_url=settings.deepseek_base_url,
        api_key=settings.deepseek_api_key,
        temperature=0.15,
        max_tokens=2200,
        enabled=bool(settings.deepseek_api_key),
        description="Deep review lane with stronger reasoning",
    ),
    ModelProfile(
        alias="rewrite_standard",
        provider_kind="openai_compatible",
        provider_name="qwen",
        model=settings.qwen_review_model,
        base_url=settings.qwen_base_url,
        api_key=settings.qwen_api_key,
        temperature=0.55,
        max_tokens=2400,
        enabled=bool(settings.qwen_api_key),
        description="Cost-aware rewrite lane",
    ),
    ModelProfile(
        alias="rewrite_quality",
        provider_kind="openai_compatible",
        provider_name="qwen",
        model=settings.qwen_rewrite_model,
        base_url=settings.qwen_base_url,
        api_key=settings.qwen_api_key,
        temperature=0.65,
        max_tokens=2800,
        enabled=bool(settings.qwen_api_key),
        description="High-quality rewrite lane",
    ),
]
