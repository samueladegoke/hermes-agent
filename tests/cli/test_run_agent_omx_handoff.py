import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


def _phase2_result(state_dir: Path):
    return SimpleNamespace(
        request_id="rid-omx",
        task_type="code",
        state_dir=state_dir,
        routing_artifact_version="candidate-0010",
        passed=True,
        score=91.0,
        verdict="GOOD",
        threshold=65.0,
        oracle_verdict="PASS",
        adv_pass_clean=True,
        adv_findings_count=0,
        delivery_gate_passed=True,
        score_card="score card",
        ref_entry={"id": "ref-1"},
        delivery_path=str(state_dir / "delivery.json"),
        fix_prompt_path=None,
        error=None,
        notes=[],
        som_score=91.0,
        eop_score=100.0,
        composite_score=94.6,
    )


@patch("run_agent.AIAgent._build_system_prompt", return_value="system prompt")
def test_run_conversation_hands_off_code_tasks_to_omx_when_enabled(mock_sys, monkeypatch, tmp_path):
    from run_agent import AIAgent

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    output_path = state_dir / "output.md"

    monkeypatch.setenv("HERMES_ENABLE_OMX_EXECUTOR", "1")

    monkeypatch.setattr(
        "gateway.meta_router_runtime.make_route_decision",
        lambda **kwargs: SimpleNamespace(
            request_id="rid-omx",
            type="code",
            mode="execute",
            directive="[META-ROUTER | code | execute]",
            bypassed=False,
            confidence=1.0,
            routing_artifact_version="candidate-0010",
        ),
    )
    monkeypatch.setattr(
        "gateway.meta_router_executor.run_phase1",
        lambda task_text, mr_type: SimpleNamespace(
            phase1_ok=True,
            state_dir=state_dir,
            targets_context="[SoM Targets | code]",
        ),
    )

    def fake_execute_request(request, *, workdir=None, command_override=None):
        output_path.write_text("def safe_divide(a, b):\n    return a / b\n", encoding="utf-8")
        Path(request["result_path"]).write_text(
            json.dumps(
                {
                    "request_id": request["request_id"],
                    "status": "completed",
                    "engine": "omx",
                    "workflow": "plain",
                    "output_path": request["output_path"],
                }
            ),
            encoding="utf-8",
        )
        return {
            "request_id": request["request_id"],
            "status": "completed",
            "engine": "omx",
            "workflow": "plain",
            "output_path": request["output_path"],
        }

    monkeypatch.setattr("gateway.omx_executor.execute_request", fake_execute_request)
    monkeypatch.setattr("gateway.meta_router_executor.run_phase2", lambda *args, **kwargs: _phase2_result(state_dir))
    monkeypatch.setattr(
        "gateway.meta_router_executor.format_routed_response",
        lambda final_response, phase2, directive="": final_response + "\n\n[OMX RECEIPT]",
    )
    monkeypatch.setattr("run_agent.AIAgent._persist_session", lambda *args, **kwargs: None)
    monkeypatch.setattr("run_agent.AIAgent._save_trajectory", lambda *args, **kwargs: None)
    monkeypatch.setattr("run_agent.AIAgent._cleanup_task_resources", lambda *args, **kwargs: None)

    def _unexpected_api(*args, **kwargs):
        raise AssertionError("model API should not be called when OMX handoff is active")

    monkeypatch.setattr("run_agent.AIAgent._interruptible_api_call", _unexpected_api)
    monkeypatch.setattr("run_agent.AIAgent._interruptible_streaming_api_call", _unexpected_api)

    agent = AIAgent(
        model="test/model",
        base_url="https://api.openai.com/v1",
        api_key="test-key",
        api_mode="chat_completions",
        quiet_mode=True,
        skip_memory=True,
        skip_context_files=True,
    )
    agent.client = None

    result = agent.run_conversation(
        user_message="Implement a Python CLI that parses CSV files and adds tests.",
        conversation_history=[],
    )

    assert result["completed"] is True
    assert "def safe_divide(a, b):" in result["final_response"]
    assert "[OMX RECEIPT]" in result["final_response"]


@patch("run_agent.AIAgent._build_system_prompt", return_value="system prompt")
def test_run_conversation_falls_back_to_normal_path_when_omx_handoff_fails(mock_sys, monkeypatch, tmp_path):
    from run_agent import AIAgent

    state_dir = tmp_path / "state"
    state_dir.mkdir()

    monkeypatch.setenv("HERMES_ENABLE_OMX_EXECUTOR", "1")

    monkeypatch.setattr(
        "gateway.meta_router_runtime.make_route_decision",
        lambda **kwargs: SimpleNamespace(
            request_id="rid-omx-fallback",
            type="code",
            mode="execute",
            directive="[META-ROUTER | code | execute]",
            bypassed=False,
            confidence=1.0,
            routing_artifact_version="candidate-0010",
        ),
    )
    monkeypatch.setattr(
        "gateway.meta_router_executor.run_phase1",
        lambda task_text, mr_type: SimpleNamespace(
            phase1_ok=True,
            state_dir=state_dir,
            targets_context="[SoM Targets | code]",
        ),
    )
    monkeypatch.setattr(
        "gateway.omx_executor.execute_request",
        lambda request, *, workdir=None, command_override=None: {
            "request_id": request["request_id"],
            "status": "failed",
            "engine": "omx",
            "workflow": "plain",
            "output_path": request["output_path"],
            "error": "omx execution did not produce output artifacts",
        },
    )
    monkeypatch.setattr("run_agent.AIAgent._persist_session", lambda *args, **kwargs: None)
    monkeypatch.setattr("run_agent.AIAgent._save_trajectory", lambda *args, **kwargs: None)
    monkeypatch.setattr("run_agent.AIAgent._cleanup_task_resources", lambda *args, **kwargs: None)
    monkeypatch.setattr("gateway.meta_router_executor.run_phase2", lambda *args, **kwargs: _phase2_result(state_dir))
    monkeypatch.setattr(
        "gateway.meta_router_executor.format_routed_response",
        lambda final_response, phase2, directive="": final_response,
    )

    class _Choice:
        def __init__(self):
            self.message = SimpleNamespace(content="normal fallback response", tool_calls=None, refusal=None, reasoning_content=None)
            self.finish_reason = "stop"

    response = SimpleNamespace(
        choices=[_Choice()],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        model="test-model",
        id="resp-fallback",
    )

    monkeypatch.setattr("run_agent.AIAgent._interruptible_streaming_api_call", lambda *args, **kwargs: response)
    monkeypatch.setattr("run_agent.AIAgent._interruptible_api_call", lambda *args, **kwargs: response)

    agent = AIAgent(
        model="test/model",
        base_url="https://api.openai.com/v1",
        api_key="test-key",
        api_mode="chat_completions",
        quiet_mode=True,
        skip_memory=True,
        skip_context_files=True,
    )
    agent.client = None

    result = agent.run_conversation(
        user_message="Implement a Python CLI that parses CSV files and adds tests.",
        conversation_history=[],
    )

    assert result["final_response"] == "normal fallback response"


@patch("run_agent.AIAgent._build_system_prompt", return_value="system prompt")
def test_run_conversation_routes_correction_pass_back_through_omx(mock_sys, monkeypatch, tmp_path):
    from run_agent import AIAgent

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "fix_prompt.md").write_text("Add the missing edge-case notes.", encoding="utf-8")

    monkeypatch.setenv("HERMES_ENABLE_OMX_EXECUTOR", "1")
    monkeypatch.setattr(
        "gateway.meta_router_runtime.make_route_decision",
        lambda **kwargs: SimpleNamespace(
            request_id="rid-omx-correct",
            type="code",
            mode="execute",
            directive="[META-ROUTER | code | execute]",
            bypassed=False,
            confidence=1.0,
            routing_artifact_version="candidate-0010",
        ),
    )
    monkeypatch.setattr(
        "gateway.meta_router_executor.run_phase1",
        lambda task_text, mr_type: SimpleNamespace(
            phase1_ok=True,
            state_dir=state_dir,
            targets_context="[SoM Targets | code]",
        ),
    )

    handoff_prompts = []

    def fake_handoff(self, correction_task_text=None):
        handoff_prompts.append(correction_task_text)
        if correction_task_text:
            return "corrected OMX response", {"status": "completed", "engine": "omx", "workflow": "plain", "output_path": str(state_dir / 'output.md')}
        return "initial OMX response", {"status": "completed", "engine": "omx", "workflow": "plain", "output_path": str(state_dir / 'output.md')}

    phase2_results = [
        SimpleNamespace(
            request_id="rid-omx-correct",
            task_type="code",
            state_dir=state_dir,
            routing_artifact_version="candidate-0010",
            passed=False,
            score=61.0,
            verdict="ACCEPTABLE",
            threshold=90.0,
            oracle_verdict="FAIL",
            adv_pass_clean=True,
            adv_findings_count=0,
            delivery_gate_passed=False,
            score_card=None,
            ref_entry=None,
            delivery_path=None,
            fix_prompt_path=str(state_dir / "fix_prompt.md"),
            error=None,
            notes=[],
            som_score=61.0,
            eop_score=100.0,
            composite_score=76.6,
        ),
        _phase2_result(state_dir),
    ]

    monkeypatch.setattr("run_agent.AIAgent._run_omx_handoff", fake_handoff)
    monkeypatch.setattr("gateway.meta_router_executor.run_phase2", lambda *args, **kwargs: phase2_results.pop(0))
    monkeypatch.setattr(
        "gateway.meta_router_executor.format_routed_response",
        lambda final_response, phase2, directive="": final_response,
    )
    monkeypatch.setattr("run_agent.AIAgent._persist_session", lambda *args, **kwargs: None)
    monkeypatch.setattr("run_agent.AIAgent._save_trajectory", lambda *args, **kwargs: None)
    monkeypatch.setattr("run_agent.AIAgent._cleanup_task_resources", lambda *args, **kwargs: None)

    def _unexpected_api(*args, **kwargs):
        raise AssertionError("LLM correction pass should not use native API when OMX handoff is active")

    monkeypatch.setattr("run_agent.AIAgent._interruptible_api_call", _unexpected_api)
    monkeypatch.setattr("run_agent.AIAgent._interruptible_streaming_api_call", _unexpected_api)

    agent = AIAgent(
        model="test/model",
        base_url="https://api.openai.com/v1",
        api_key="test-key",
        api_mode="chat_completions",
        quiet_mode=True,
        skip_memory=True,
        skip_context_files=True,
    )
    agent.client = None

    result = agent.run_conversation(
        user_message="Implement a Python CLI that parses CSV files and adds tests.",
        conversation_history=[],
    )

    assert result["final_response"] == "corrected OMX response"
    assert len(handoff_prompts) == 2
    assert handoff_prompts[0] is None
    assert "CORRECTION PASS 1/3" in handoff_prompts[1]
    assert "Add the missing edge-case notes." in handoff_prompts[1]


@patch("run_agent.AIAgent._build_system_prompt", return_value="system prompt")
def test_run_conversation_keeps_normal_path_when_omx_disabled(mock_sys, monkeypatch):
    from run_agent import AIAgent

    monkeypatch.delenv("HERMES_ENABLE_OMX_EXECUTOR", raising=False)
    monkeypatch.setattr(
        "gateway.meta_router_runtime.make_route_decision",
        lambda **kwargs: SimpleNamespace(
            request_id="rid-normal",
            type="code",
            mode="execute",
            directive="[META-ROUTER | code | execute]",
            bypassed=False,
            confidence=1.0,
            routing_artifact_version="candidate-0010",
        ),
    )
    monkeypatch.setattr(
        "gateway.meta_router_executor.run_phase1",
        lambda task_text, mr_type: SimpleNamespace(
            phase1_ok=True,
            state_dir=None,
            targets_context="[SoM Targets | code]",
        ),
    )
    monkeypatch.setattr("run_agent.AIAgent._persist_session", lambda *args, **kwargs: None)
    monkeypatch.setattr("run_agent.AIAgent._save_trajectory", lambda *args, **kwargs: None)
    monkeypatch.setattr("run_agent.AIAgent._cleanup_task_resources", lambda *args, **kwargs: None)

    class _Choice:
        def __init__(self):
            self.message = SimpleNamespace(content="normal response", tool_calls=None, refusal=None, reasoning_content=None)
            self.finish_reason = "stop"

    response = SimpleNamespace(
        choices=[_Choice()],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        model="test-model",
        id="resp-1",
    )

    monkeypatch.setattr("run_agent.AIAgent._interruptible_streaming_api_call", lambda *args, **kwargs: response)
    monkeypatch.setattr("run_agent.AIAgent._interruptible_api_call", lambda *args, **kwargs: response)

    agent = AIAgent(
        model="test/model",
        base_url="https://api.openai.com/v1",
        api_key="test-key",
        api_mode="chat_completions",
        quiet_mode=True,
        skip_memory=True,
        skip_context_files=True,
    )
    agent.client = None

    result = agent.run_conversation(
        user_message="Implement a Python CLI that parses CSV files and adds tests.",
        conversation_history=[],
    )

    assert result["final_response"] == "normal response"


@patch("run_agent.AIAgent._build_system_prompt", return_value="system prompt")
def test_native_correction_pass_is_internal_and_not_persisted(mock_sys, monkeypatch, tmp_path):
    from run_agent import AIAgent

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "fix_prompt.md").write_text("Add the missing verification section.", encoding="utf-8")

    route_calls = []

    def fake_route(**kwargs):
        route_calls.append(kwargs["text"])
        return SimpleNamespace(
            request_id="rid-native-correct",
            type="research",
            mode="execute",
            directive="[META-ROUTER | research | execute]",
            bypassed=False,
            confidence=1.0,
            routing_artifact_version="candidate-0011",
        )

    monkeypatch.setattr("gateway.meta_router_runtime.make_route_decision", fake_route)
    monkeypatch.setattr(
        "gateway.meta_router_executor.run_phase1",
        lambda task_text, mr_type: SimpleNamespace(
            phase1_ok=True,
            state_dir=state_dir,
            targets_context="[SoM Targets | research]",
        ),
    )

    phase2_results = [
        SimpleNamespace(
            request_id="rid-native-correct",
            task_type="research",
            state_dir=state_dir,
            routing_artifact_version="candidate-0011",
            passed=False,
            score=76.0,
            verdict="GOOD",
            threshold=70.0,
            oracle_verdict="PASS",
            adv_pass_clean=True,
            adv_findings_count=0,
            delivery_gate_passed=False,
            score_card=None,
            ref_entry=None,
            delivery_path=None,
            fix_prompt_path=str(state_dir / "fix_prompt.md"),
            error=None,
            notes=[],
            som_score=76.0,
            eop_score=100.0,
            composite_score=85.6,
        ),
        _phase2_result(state_dir),
    ]
    monkeypatch.setattr("gateway.meta_router_executor.run_phase2", lambda *args, **kwargs: phase2_results.pop(0))
    monkeypatch.setattr(
        "gateway.meta_router_executor.format_routed_response",
        lambda final_response, phase2, directive="": final_response,
    )

    persisted_batches = []

    def fake_persist(self, messages, conversation_history=None):
        persisted_batches.append([dict(m) for m in messages])

    monkeypatch.setattr("run_agent.AIAgent._persist_session", fake_persist)
    monkeypatch.setattr("run_agent.AIAgent._save_trajectory", lambda *args, **kwargs: None)
    monkeypatch.setattr("run_agent.AIAgent._cleanup_task_resources", lambda *args, **kwargs: None)

    responses = iter([
        SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(content="initial native response", tool_calls=None, refusal=None, reasoning_content=None),
                finish_reason="stop",
            )],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            model="test-model",
            id="resp-initial",
        ),
        SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(content="corrected native response", tool_calls=None, refusal=None, reasoning_content=None),
                finish_reason="stop",
            )],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            model="test-model",
            id="resp-corrected",
        ),
    ])
    monkeypatch.setattr("run_agent.AIAgent._interruptible_streaming_api_call", lambda *args, **kwargs: next(responses))
    monkeypatch.setattr("run_agent.AIAgent._interruptible_api_call", lambda *args, **kwargs: next(responses))

    agent = AIAgent(
        model="test/model",
        base_url="https://api.openai.com/v1",
        api_key="test-key",
        api_mode="chat_completions",
        quiet_mode=True,
        skip_memory=True,
        skip_context_files=True,
    )
    agent.client = None

    result = agent.run_conversation(
        user_message="Research the live evidence and summarize the fix.",
        conversation_history=[],
    )

    assert result["final_response"] == "corrected native response"
    assert route_calls == ["Research the live evidence and summarize the fix."]
    assert persisted_batches
    persisted_text = json.dumps(persisted_batches)
    assert "[MR correction pass — not user-visible]" not in persisted_text
    assert "CORRECTION PASS 1/2" not in persisted_text


def test_run_conversation_has_one_meta_router_post_turn_block():
    import inspect
    from run_agent import AIAgent

    src = inspect.getsource(AIAgent.run_conversation)

    assert src.count("MR-ALS post-turn: SoM Phase 2 scoring") == 1
