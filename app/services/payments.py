from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy.orm import Session

from ..config import settings
from ..models import PaymentOrder
from ..schemas import PaymentCreationResult
from .billing import billing_service


class PaymentService:
    def create_order(
        self,
        session: Session,
        user_id: int,
        amount_cents: int,
        provider: str,
        channel: str,
    ) -> tuple[PaymentOrder, PaymentCreationResult]:
        if not self.is_provider_allowed(provider):
            raise RuntimeError("当前未开放该支付方式。")
        order = PaymentOrder(
            user_id=user_id,
            order_no=str(uuid4()),
            provider=provider,
            channel=channel,
            amount_cents=amount_cents,
            description=f"充值 {amount_cents / 100:.2f} 元",
        )
        session.add(order)
        session.commit()
        session.refresh(order)

        creation_result = self._provider(provider).create_payment(order)
        order.redirect_url = creation_result.redirect_url
        order.qr_code = creation_result.qr_code
        order.provider_order_id = creation_result.provider_order_id
        session.add(order)
        session.commit()
        session.refresh(order)
        return order, creation_result

    def mark_paid(self, session: Session, order_no: str, provider_order_id: str | None = None) -> PaymentOrder:
        order = session.query(PaymentOrder).filter(PaymentOrder.order_no == order_no).one()
        if order.status == "paid":
            return order
        order.status = "paid"
        order.paid_at = datetime.utcnow()
        if provider_order_id:
            order.provider_order_id = provider_order_id
        session.add(order)
        session.commit()
        session.refresh(order)

        wallet = billing_service.ensure_wallet(session, order.user_id)
        billing_service.credit_wallet(session, wallet, order.amount_cents, f"充值到账 {order.order_no}")
        return order

    def _provider(self, provider: str):
        if not self.is_provider_allowed(provider):
            raise RuntimeError("当前未开放该支付方式。")
        if provider == "alipay":
            from ..providers.payments.alipay_page import AlipayPageProvider

            return AlipayPageProvider()
        if provider == "wechat":
            from ..providers.payments.wechat_native import WechatNativeProvider

            return WechatNativeProvider()
        from ..providers.payments.mock import MockPaymentProvider

        return MockPaymentProvider()

    def is_provider_allowed(self, provider: str) -> bool:
        if provider == "mock":
            return settings.enable_mock_topup
        if not settings.enable_payments:
            return False
        return provider in {"alipay", "wechat"}

    def available_providers(self) -> list[dict[str, str]]:
        providers: list[dict[str, str]] = []
        if settings.enable_mock_topup:
            providers.append({"value": "mock", "label": "模拟充值（测试）"})
        if settings.enable_payments:
            providers.extend(
                [
                    {"value": "alipay", "label": "支付宝网页支付"},
                    {"value": "wechat", "label": "微信 Native 支付"},
                ]
            )
        return providers


payment_service = PaymentService()
