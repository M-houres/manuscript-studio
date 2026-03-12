from app.services.pricing import pricing_service
from app.services.review import review_service
from app.services.rewrite import rewrite_service


def test_pricing_counts_visible_chars_only():
    quote = pricing_service.quote("review", "A B\nC")
    assert quote.char_count == 3
    assert quote.total_price_cents == 69


def test_review_returns_structure_findings():
    report = review_service.generate(
        "Test Title",
        "The opening paragraph starts directly with discussion.\nAnother paragraph continues the discussion without a conclusion section.",
        "review_deep",
    )
    categories = {issue.category for issue in report.issues}
    assert "结构审稿" in categories
    assert report.overall_score < 90


def test_rewrite_produces_text_and_diff():
    result = rewrite_service.optimize(
        "Test Title",
        "其实这段文字非常长，而且其实表达有点重复。",
        "standard",
        "rewrite_quality",
    )
    assert result.optimized_text
    assert result.diff_blocks
