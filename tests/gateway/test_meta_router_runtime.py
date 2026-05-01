import gateway.meta_router_runtime as runtime


def _capture_events(monkeypatch):
    events = []
    monkeypatch.setattr(runtime, "_init_logger", lambda: None)
    monkeypatch.setattr(runtime, "_ALS_LOGGING", True)
    monkeypatch.setattr(runtime, "_log_event_fn", lambda **kwargs: events.append(kwargs))
    monkeypatch.setattr(runtime, "_make_request_id_fn", lambda: "rid-test")
    return events


def test_make_route_decision_bypasses_short_acknowledgements(monkeypatch):
    _capture_events(monkeypatch)

    decision = runtime.make_route_decision("ok", source="gateway", surface="telegram")

    assert decision.bypassed is True
    assert decision.bypass_reason == "short-ack"
    assert decision.directive == ""


def test_make_route_decision_bypasses_commands(monkeypatch):
    _capture_events(monkeypatch)

    decision = runtime.make_route_decision("/status", source="gateway", surface="telegram")

    assert decision.bypassed is True
    assert decision.bypass_reason == "command"
    assert decision.directive == ""


def test_make_route_decision_logs_requested_source_and_surface(monkeypatch):
    events = _capture_events(monkeypatch)

    decision = runtime.make_route_decision(
        "deploy hotfix to production immediately",
        source="gateway",
        surface="telegram",
        session_id="sess-123",
    )

    assert decision.bypassed is False
    assert decision.routing_artifact_version
    assert events, "expected a routing event to be emitted"
    assert events[-1]["source"] == "gateway"
    assert events[-1]["surface"] == "telegram"
    assert events[-1]["session_id"] == "sess-123"
