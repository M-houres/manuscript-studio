from __future__ import annotations

from typing import Protocol

from ...models import PaymentOrder
from ...schemas import PaymentCreationResult


class PaymentProvider(Protocol):
    def create_payment(self, order: PaymentOrder) -> PaymentCreationResult:
        ...
