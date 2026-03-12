from __future__ import annotations

from ...config import settings
from ...models import PaymentOrder
from ...schemas import PaymentCreationResult


class AlipayPageProvider:
    def create_payment(self, order: PaymentOrder) -> PaymentCreationResult:
        try:
            from alipay.api import AliPay
        except ModuleNotFoundError as exc:
            raise RuntimeError("Alipay SDK is installed incompletely. Please verify merchant SDK dependencies before enabling this channel.") from exc

        if not (settings.alipay_app_id and settings.alipay_private_key and settings.alipay_public_key):
            raise RuntimeError("Alipay is not configured yet.")

        client = AliPay(
            clientid=settings.alipay_app_id,
            private_key=settings.alipay_private_key,
            public_key=settings.alipay_public_key,
            return_url=settings.alipay_return_url,
            notify_url=settings.alipay_notify_url,
            sign_type="RSA2",
        )
        redirect_url = client.pay.trade_page_pay(
            out_trade_no=order.order_no,
            total_amount=order.amount_cents / 100,
            subject=order.description,
        )
        return PaymentCreationResult(
            provider="alipay",
            channel=order.channel,
            redirect_url=redirect_url,
            qr_code=None,
            provider_order_id=order.order_no,
            raw_payload={"redirect_url": redirect_url},
        )
