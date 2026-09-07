import pytest

from llm_analyst import (
    TradeVerdict,
    parse_verdict_from_text,
    extract_json_object,
)


def test_valid_json():
    verdict = parse_verdict_from_text(
        '{"decision": "CONFIRM", "confidence": 0.95, "reason": "strong bullish setup"}'
    )
    assert isinstance(verdict, TradeVerdict)
    assert verdict.decision == "CONFIRM"
    assert verdict.confidence == pytest.approx(0.95)


def test_markdown_wrapped_json():
    raw = """Sure, here is the verdict:
```json
{"decision": "REJECT", "confidence": 0.4, "reason": "bull trap likely"}
```
Hope this helps."""
    verdict = parse_verdict_from_text(raw)
    assert verdict.decision == "REJECT"
    assert verdict.confidence == pytest.approx(0.4)
    assert "bull trap" in verdict.reason


def test_plain_triple_backtick_json():
    raw = '```\n{"decision": "CONFIRM", "confidence": 0.8, "reason": "good entry"}\n```'
    verdict = parse_verdict_from_text(raw)
    assert verdict.decision == "CONFIRM"


def test_json_with_loose_text():
    raw = "The answer is {\"decision\": \"CONFIRM\", \"confidence\": 0.7, \"reason\": \"decent setup\"} end"
    verdict = parse_verdict_from_text(raw)
    assert verdict.decision == "CONFIRM"


def test_broken_json_raises():
    with pytest.raises(ValueError):
        parse_verdict_from_text("this is not json at all")


def test_missing_field_raises():
    with pytest.raises(ValueError):
        parse_verdict_from_text('{"decision": "CONFIRM"}')


def test_confidence_out_of_range_raises():
    with pytest.raises(ValueError):
        TradeVerdict(decision="CONFIRM", confidence=1.5, reason="too confident")


def test_confidence_negative_raises():
    with pytest.raises(ValueError):
        TradeVerdict(decision="CONFIRM", confidence=-0.1, reason="bad confidence")


def test_reason_too_short_raises():
    with pytest.raises(ValueError):
        TradeVerdict(decision="CONFIRM", confidence=0.9, reason="ab")


def test_invalid_decision_raises():
    with pytest.raises(ValueError):
        TradeVerdict(decision="MAYBE", confidence=0.9, reason="indecisive judgment")


def test_extract_returns_none_for_empty():
    assert extract_json_object("") is None
    assert extract_json_object("   ") is None