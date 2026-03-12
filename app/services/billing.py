from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import WalletAccount, WalletLedgerEntry


class BillingService:
    def ensure_wallet(self, session: Session, user_id: int) -> WalletAccount:
        wallet = session.query(WalletAccount).filter(WalletAccount.user_id == user_id).one_or_none()
        if wallet is None:
            wallet = WalletAccount(user_id=user_id)
            session.add(wallet)
            session.commit()
            session.refresh(wallet)
        return wallet

    def credit_wallet(self, session: Session, wallet: WalletAccount, amount_cents: int, description: str) -> WalletAccount:
        wallet.balance_cents += amount_cents
        wallet.total_recharged_cents += amount_cents
        session.add(WalletLedgerEntry(
            wallet_id=wallet.id,
            entry_type="topup",
            amount_cents=amount_cents,
            balance_after_cents=wallet.balance_cents,
            description=description,
        ))
        session.add(wallet)
        session.commit()
        session.refresh(wallet)
        return wallet

    def spend(self, session: Session, wallet: WalletAccount, amount_cents: int, description: str) -> WalletAccount:
        if wallet.balance_cents < amount_cents:
            raise ValueError("Insufficient balance.")
        wallet.balance_cents -= amount_cents
        wallet.total_spent_cents += amount_cents
        session.add(WalletLedgerEntry(
            wallet_id=wallet.id,
            entry_type="spend",
            amount_cents=-amount_cents,
            balance_after_cents=wallet.balance_cents,
            description=description,
        ))
        session.add(wallet)
        session.commit()
        session.refresh(wallet)
        return wallet


billing_service = BillingService()
