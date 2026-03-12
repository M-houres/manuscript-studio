from __future__ import annotations

from ...models import PaymentOrder
from ...schemas import PaymentCreationResult


class MockPaymentProvider:
    def create_payment(self, order: PaymentOrder) -> PaymentCreationResult:
        return PaymentCreationResult(
            provider="mock",
            channel=order.channel,
            redirect_url=f"/payments/mock/{order.order_no}/pay",
            qr_code=None,
            provider_order_id=order.order_no,
            raw_payload={"message": "Mock payment created."},
        )
