from __future__ import annotations

from dataclasses import dataclass

from ..config import settings


@dataclass(slots=True)
class PriceQuote:
    feature: str
    char_count: int
    unit_price_cents: int
    total_price_cents: int


class PricingService:
    def count_billable_chars(self, text: str) -> int:
        return sum(1 for ch in text if not ch.isspace())

    def quote(self, feature: str, text: str) -> PriceQuote:
        char_count = self.count_billable_chars(text)
        if char_count <= 0:
            return PriceQuote(feature=feature, char_count=0, unit_price_cents=0, total_price_cents=0)

        unit_price = settings.review_price_per_1k_chars_cents if feature == "review" else settings.rewrite_price_per_1k_chars_cents
        blocks = (char_count + 999) // 1000
        return PriceQuote(
            feature=feature,
            char_count=char_count,
            unit_price_cents=unit_price,
            total_price_cents=blocks * unit_price,
        )


pricing_service = PricingService()
