from __future__ import annotations

import json

from wechatpayv3 import WeChatPay, WeChatPayType

from ...config import settings
from ...models import PaymentOrder
from ...schemas import PaymentCreationResult


class WechatNativeProvider:
    def create_payment(self, order: PaymentOrder) -> PaymentCreationResult:
        if not all([
            settings.wechat_mchid,
            settings.wechat_private_key,
            settings.wechat_cert_serial_no,
            settings.wechat_appid,
            settings.wechat_apiv3_key,
        ]):
            raise RuntimeError("WeChat Pay is not configured yet.")

        client = WeChatPay(
            wechatpay_type=WeChatPayType.NATIVE,
            mchid=settings.wechat_mchid,
            private_key=settings.wechat_private_key,
            cert_serial_no=settings.wechat_cert_serial_no,
            appid=settings.wechat_appid,
            apiv3_key=settings.wechat_apiv3_key,
            notify_url=settings.wechat_notify_url,
            public_key=settings.wechat_public_key or None,
            public_key_id=settings.wechat_public_key_id or None,
        )
        status_code, content = client.pay(
            description=order.description,
            out_trade_no=order.order_no,
            amount={"total": order.amount_cents, "currency": "CNY"},
            pay_type=WeChatPayType.NATIVE,
        )
        if status_code not in range(200, 300):
            raise RuntimeError(f"WeChat Pay create order failed: {content}")
        data = json.loads(content)
        return PaymentCreationResult(
            provider="wechat",
            channel=order.channel,
            redirect_url=None,
            qr_code=data.get("code_url"),
            provider_order_id=data.get("prepay_id") or order.order_no,
            raw_payload=data,
        )
