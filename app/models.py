from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))

    wallet: Mapped["WalletAccount"] = relationship(back_populates="user", uselist=False)
    documents: Mapped[list["DocumentRecord"]] = relationship(back_populates="user")
    runs: Mapped[list["AnalysisRun"]] = relationship(back_populates="user")
    payment_orders: Mapped[list["PaymentOrder"]] = relationship(back_populates="user")


class BonusClaim(TimestampMixin, Base):
    __tablename__ = "bonus_claims"
    __table_args__ = (UniqueConstraint("user_id", name="uq_bonus_claim_user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    amount_cents: Mapped[int] = mapped_column(Integer)
    note: Mapped[str] = mapped_column(String(255), default="内测领取")


class ModelCallLog(TimestampMixin, Base):
    __tablename__ = "model_call_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    alias: Mapped[str] = mapped_column(String(64), index=True)
    provider_name: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(128))
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(String(255), nullable=True)


class AdminAudit(TimestampMixin, Base):
    __tablename__ = "admin_audits"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_email: Mapped[str] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(64))
    target: Mapped[str] = mapped_column(String(255), default="")
    amount_cents: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str] = mapped_column(String(255), default="")


class ModelConfig(TimestampMixin, Base):
    __tablename__ = "model_configs"
    __table_args__ = (UniqueConstraint("alias", name="uq_model_config_alias"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    alias: Mapped[str] = mapped_column(String(64), index=True)
    provider_kind: Mapped[str] = mapped_column(String(64), default="openai_compatible")
    provider_name: Mapped[str] = mapped_column(String(64), default="custom")
    model: Mapped[str] = mapped_column(String(128))
    base_url: Mapped[str] = mapped_column(String(256))
    api_key: Mapped[str] = mapped_column(String(256), default="")
    temperature: Mapped[float] = mapped_column(Float, default=0.0)
    max_tokens: Mapped[int] = mapped_column(Integer, default=2000)
    enabled: Mapped[int] = mapped_column(Integer, default=1)
    description: Mapped[str] = mapped_column(String(255), default="")


class WalletAccount(TimestampMixin, Base):
    __tablename__ = "wallet_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    balance_cents: Mapped[int] = mapped_column(Integer, default=0)
    total_recharged_cents: Mapped[int] = mapped_column(Integer, default=0)
    total_spent_cents: Mapped[int] = mapped_column(Integer, default=0)

    user: Mapped[User] = relationship(back_populates="wallet")
    ledger_entries: Mapped[list["WalletLedgerEntry"]] = relationship(back_populates="wallet")


class WalletLedgerEntry(Base):
    __tablename__ = "wallet_ledger_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    wallet_id: Mapped[int] = mapped_column(ForeignKey("wallet_accounts.id"), index=True)
    entry_type: Mapped[str] = mapped_column(String(32))
    amount_cents: Mapped[int] = mapped_column(Integer)
    balance_after_cents: Mapped[int] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    wallet: Mapped[WalletAccount] = relationship(back_populates="ledger_entries")


class DocumentRecord(TimestampMixin, Base):
    __tablename__ = "document_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    source_name: Mapped[str] = mapped_column(String(255), default="manual-input")
    text_content: Mapped[str] = mapped_column(Text)
    char_count: Mapped[int] = mapped_column(Integer)

    user: Mapped[User] = relationship(back_populates="documents")
    runs: Mapped[list["AnalysisRun"]] = relationship(back_populates="document")


class AnalysisRun(TimestampMixin, Base):
    __tablename__ = "analysis_runs"
    __table_args__ = (UniqueConstraint("run_no", name="uq_analysis_run_no"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_no: Mapped[str] = mapped_column(String(36), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("document_records.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="completed")
    billed_chars: Mapped[int] = mapped_column(Integer)
    unit_price_cents: Mapped[int] = mapped_column(Integer)
    total_price_cents: Mapped[int] = mapped_column(Integer)
    model_alias: Mapped[str] = mapped_column(String(64))
    provider_name: Mapped[str] = mapped_column(String(64), default="heuristic")
    result_json: Mapped[str] = mapped_column(Text)
    completed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped[User] = relationship(back_populates="runs")
    document: Mapped[DocumentRecord] = relationship(back_populates="runs")


class PaymentOrder(TimestampMixin, Base):
    __tablename__ = "payment_orders"
    __table_args__ = (UniqueConstraint("order_no", name="uq_payment_order_no"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    order_no: Mapped[str] = mapped_column(String(36), index=True)
    provider: Mapped[str] = mapped_column(String(32))
    channel: Mapped[str] = mapped_column(String(32))
    amount_cents: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    status: Mapped[str] = mapped_column(String(32), default="pending")
    description: Mapped[str] = mapped_column(String(255))
    redirect_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    qr_code: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    provider_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped[User] = relationship(back_populates="payment_orders")
