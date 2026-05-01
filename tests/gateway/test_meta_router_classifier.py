import sys
import types
from types import SimpleNamespace

import gateway.meta_router as meta_router
import gateway.meta_router_llm as meta_router_llm


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


class _FakeResponsesClient:
    def __init__(self):
        self.kwargs = None

    def stream(self, **kwargs):
        self.kwargs = kwargs
        return _FakeStream('{"type": "integration", "mode": "review", "confidence": 0.9, "reasoning": "ok"}')


class _FakeStream:
    def __init__(self, text):
        self.text = text

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def __iter__(self):
        yield SimpleNamespace(type="response.output_text.delta", delta=self.text)

    def get_final_response(self):
        return SimpleNamespace(output_text=self.text)



def test_llm_classify_sends_responses_input_as_list(monkeypatch):
    fake_client = _FakeResponsesClient()

    monkeypatch.setattr(meta_router_llm, "_build_client", lambda timeout_seconds: SimpleNamespace(responses=fake_client))

    result = meta_router_llm.llm_classify(
        LOW_CONFIDENCE_TASK,
        meta_router.RouteResult(
            type="integration",
            mode="execute",
            confidence=0.429,
            directive="[META-ROUTER | integration | execute]",
        ),
    )

    assert isinstance(fake_client.kwargs["input"], list)
    assert fake_client.kwargs["input"][0]["role"] == "user"
    assert fake_client.kwargs["input"][0]["content"][0]["type"] == "input_text"
    assert result.type == "integration"
    assert result.mode == "review"
