from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = BASE_DIR / "uploads"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name: str) -> list[str]:
    raw = os.getenv(name, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


def _env_optional(name: str, default: str | None = None) -> str | None:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip()
    return value or default


@dataclass(slots=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "Manuscript Studio")
    environment: str = os.getenv("APP_ENV", "development")
    secret_key: str = os.getenv("APP_SECRET_KEY", "change-me-in-production")
    database_url: str = os.getenv("DATABASE_URL", f"sqlite:///{(DATA_DIR / 'app.db').as_posix()}")
    upload_dir: Path = Path(os.getenv("UPLOAD_DIR", str(UPLOAD_DIR)))
    max_upload_mb: int = int(os.getenv("MAX_UPLOAD_MB", "10"))

    review_price_per_1k_chars_cents: int = int(os.getenv("REVIEW_PRICE_PER_1K_CHARS_CENTS", "69"))
    rewrite_price_per_1k_chars_cents: int = int(os.getenv("REWRITE_PRICE_PER_1K_CHARS_CENTS", "199"))
    combo_discount_percent: int = int(os.getenv("COMBO_DISCOUNT_PERCENT", "10"))
    signup_bonus_cents: int = int(os.getenv("SIGNUP_BONUS_CENTS", "0"))
    internal_bonus_cents: int = int(os.getenv("INTERNAL_BONUS_CENTS", "0"))

    default_review_model_alias: str = os.getenv("DEFAULT_REVIEW_MODEL_ALIAS", "review_deep")
    default_rewrite_model_alias: str = os.getenv("DEFAULT_REWRITE_MODEL_ALIAS", "rewrite_quality")
    llm_timeout_seconds: int = int(os.getenv("LLM_TIMEOUT_SECONDS", "60"))

    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    log_file: str | None = _env_optional("LOG_FILE", str(DATA_DIR / "logs" / "app.log"))

    session_cookie_name: str = os.getenv("SESSION_COOKIE_NAME", "ms_session")
    session_cookie_samesite: str = os.getenv("SESSION_COOKIE_SAMESITE", "lax")
    session_cookie_secure: bool = _env_bool("SESSION_COOKIE_SECURE", os.getenv("APP_ENV", "development") == "production")
    session_cookie_max_age: int = int(os.getenv("SESSION_COOKIE_MAX_AGE", "1209600"))
    csrf_enabled: bool = _env_bool("CSRF_ENABLED", True)
    hsts_enabled: bool = _env_bool("HSTS_ENABLED", os.getenv("APP_ENV", "development") == "production")
    allowed_hosts: list[str] = field(default_factory=lambda: _env_list("APP_ALLOWED_HOSTS") or ["*"])
    admin_emails: list[str] = field(default_factory=lambda: _env_list("ADMIN_EMAILS"))
    super_admin_emails: list[str] = field(default_factory=lambda: _env_list("SUPER_ADMIN_EMAILS"))

    app_base_url: str = os.getenv("APP_BASE_URL", "http://localhost:8000")

    smtp_host: str = os.getenv("SMTP_HOST", "")
    smtp_port: int = int(os.getenv("SMTP_PORT", "587"))
    smtp_user: str = os.getenv("SMTP_USER", "")
    smtp_password: str = os.getenv("SMTP_PASSWORD", "")
    smtp_sender: str = os.getenv("SMTP_SENDER", "")
    smtp_use_tls: bool = _env_bool("SMTP_USE_TLS", True)

    task_timeout_seconds: int = int(os.getenv("TASK_TIMEOUT_SECONDS", "75"))

    enable_payments: bool = _env_bool("ENABLE_PAYMENTS", False)
    enable_mock_topup: bool = _env_bool("ENABLE_MOCK_TOPUP", False)

    rate_limit_enabled: bool = _env_bool("RATE_LIMIT_ENABLED", True)
    rate_limit_requests: int = int(os.getenv("RATE_LIMIT_REQUESTS", "120"))
    rate_limit_window_seconds: int = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))
    rate_limit_auth_requests: int = int(os.getenv("RATE_LIMIT_AUTH_REQUESTS", "12"))
    rate_limit_auth_window_seconds: int = int(os.getenv("RATE_LIMIT_AUTH_WINDOW_SECONDS", "300"))

    payment_provider: str = os.getenv("PAYMENT_PROVIDER", "mock")
    alipay_app_id: str = os.getenv("ALIPAY_APP_ID", "")
    alipay_private_key: str = os.getenv("ALIPAY_PRIVATE_KEY", "")
    alipay_public_key: str = os.getenv("ALIPAY_PUBLIC_KEY", "")
    alipay_return_url: str = os.getenv("ALIPAY_RETURN_URL", "http://localhost:8000/payments/return")
    alipay_notify_url: str = os.getenv("ALIPAY_NOTIFY_URL", "http://localhost:8000/payments/callback/alipay")

    wechat_mchid: str = os.getenv("WECHAT_MCHID", "")
    wechat_private_key: str = os.getenv("WECHAT_PRIVATE_KEY", "")
    wechat_cert_serial_no: str = os.getenv("WECHAT_CERT_SERIAL_NO", "")
    wechat_appid: str = os.getenv("WECHAT_APPID", "")
    wechat_apiv3_key: str = os.getenv("WECHAT_APIV3_KEY", "")
    wechat_notify_url: str = os.getenv("WECHAT_NOTIFY_URL", "http://localhost:8000/payments/callback/wechat")
    wechat_public_key: str = os.getenv("WECHAT_PUBLIC_KEY", "")
    wechat_public_key_id: str = os.getenv("WECHAT_PUBLIC_KEY_ID", "")

    qwen_api_key: str = os.getenv("QWEN_API_KEY", "")
    qwen_base_url: str = os.getenv("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    qwen_review_model: str = os.getenv("QWEN_REVIEW_MODEL", "qwen-plus")
    qwen_rewrite_model: str = os.getenv("QWEN_REWRITE_MODEL", "qwen-max")

    deepseek_api_key: str = os.getenv("DEEPSEEK_API_KEY", "")
    deepseek_base_url: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
    deepseek_review_model: str = os.getenv("DEEPSEEK_REVIEW_MODEL", "deepseek-chat")
    deepseek_rewrite_model: str = os.getenv("DEEPSEEK_REWRITE_MODEL", "deepseek-chat")

    zhipu_api_key: str = os.getenv("ZHIPU_API_KEY", "")
    zhipu_base_url: str = os.getenv("ZHIPU_BASE_URL", "")
    zhipu_review_model: str = os.getenv("ZHIPU_REVIEW_MODEL", "")
    zhipu_rewrite_model: str = os.getenv("ZHIPU_REWRITE_MODEL", "")

    def ensure_dirs(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.upload_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.ensure_dirs()
