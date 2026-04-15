import sys
import types

import gateway.meta_router as meta_router


LOW_CONFIDENCE_TASK = "Explain API OAuth integration config setup"
HIGH_CONFIDENCE_TASK = "Urgent production incident rollback now"


def test_classify_uses_llm_fallback_when_keyword_confidence_is_low(monkeypatch):
    calls = []

    def fake_llm(task, keyword_result):
        calls.append((task, keyword_result))
        return meta_router.RouteResult(
            type="integration",
            mode="review",
            confidence=0.91,
            directive="[META-ROUTER | integration | review]",
        )

    monkeypatch.setitem(
        sys.modules,
        "gateway.meta_router_llm",
        types.SimpleNamespace(llm_classify=fake_llm),
    )

    result = meta_router.classify(LOW_CONFIDENCE_TASK)

    assert calls, "expected LLM fallback to run for low-confidence classification"
    assert calls[0][0] == LOW_CONFIDENCE_TASK
    assert calls[0][1].confidence < 0.5
    assert result.type == "integration"
    assert result.mode == "review"
    assert result.confidence == 0.91


def test_classify_skips_llm_fallback_when_keyword_confidence_is_high(monkeypatch):
    calls = []

    def fake_llm(task, keyword_result):
        calls.append((task, keyword_result))
        return meta_router.RouteResult(
            type="research",
            mode="plan",
            confidence=0.99,
            directive="[META-ROUTER | research | plan]",
        )

    monkeypatch.setitem(
        sys.modules,
        "gateway.meta_router_llm",
        types.SimpleNamespace(llm_classify=fake_llm),
    )

    result = meta_router.classify(HIGH_CONFIDENCE_TASK)

    assert calls == []
    assert result.type == "production"
    assert result.mode == "urgent"
    assert result.confidence >= 0.5


def test_classify_preserves_keyword_result_when_llm_fails(monkeypatch):
    baseline = meta_router.classify(LOW_CONFIDENCE_TASK)

    monkeypatch.setitem(
        sys.modules,
        "gateway.meta_router_llm",
        types.SimpleNamespace(llm_classify=lambda task, keyword_result: None),
    )

    result = meta_router.classify(LOW_CONFIDENCE_TASK)

    assert result == baseline
