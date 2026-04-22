import os
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
    monkeypatch.setattr("run_agent.AIAgent._interruptible_api_call", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("API path should not be used")))
    monkeypatch.setattr("run_agent.AIAgent._interruptible_streaming_api_call", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("API path should not be used")))

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
def test_run_conversation_does_not_handoff_review_mode_code_tasks_to_omx(mock_sys, monkeypatch, tmp_path):
    from run_agent import AIAgent

    state_dir = tmp_path / "state"
    state_dir.mkdir()

    monkeypatch.setenv("HERMES_ENABLE_OMX_EXECUTOR", "1")

    monkeypatch.setattr(
        "gateway.meta_router_runtime.make_route_decision",
        lambda **kwargs: SimpleNamespace(
            request_id="rid-review",
            type="code",
            mode="review",
            directive="[META-ROUTER | code | review]",
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
    calls = {"omx": False}

    def _unexpected_execute_request(*args, **kwargs):
        calls["omx"] = True
        raise AssertionError("OMX handoff should not run for review mode")

    monkeypatch.setattr("gateway.omx_executor.execute_request", _unexpected_execute_request)
    monkeypatch.setattr("gateway.meta_router_executor.run_phase2", lambda *args, **kwargs: _phase2_result(state_dir))
    monkeypatch.setattr(
        "gateway.meta_router_executor.format_routed_response",
        lambda final_response, phase2, directive="": final_response,
    )
    monkeypatch.setattr("run_agent.AIAgent._persist_session", lambda *args, **kwargs: None)
    monkeypatch.setattr("run_agent.AIAgent._save_trajectory", lambda *args, **kwargs: None)
    monkeypatch.setattr("run_agent.AIAgent._cleanup_task_resources", lambda *args, **kwargs: None)

    class _Choice:
        def __init__(self):
            self.message = SimpleNamespace(content="review only response", tool_calls=None, refusal=None, reasoning_content=None)
            self.finish_reason = "stop"

    response = SimpleNamespace(
        choices=[_Choice()],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        model="test-model",
        id="resp-review",
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
        user_message="In the current directory, create a tiny Python helper and verify it.",
        conversation_history=[],
    )

    assert calls["omx"] is False
    assert result["final_response"] == "review only response"


@patch("run_agent.AIAgent._build_system_prompt", return_value="system prompt")
def test_run_conversation_review_mode_filters_mutating_tools(mock_sys, monkeypatch, tmp_path):
    from run_agent import AIAgent

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    captured = {}

    monkeypatch.setenv("HERMES_ENABLE_OMX_EXECUTOR", "1")

    monkeypatch.setattr(
        "gateway.meta_router_runtime.make_route_decision",
        lambda **kwargs: SimpleNamespace(
            request_id="rid-review-filter",
            type="code",
            mode="review",
            directive="[META-ROUTER | code | review]",
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
    monkeypatch.setattr("gateway.meta_router_executor.run_phase2", lambda *args, **kwargs: _phase2_result(state_dir))
    monkeypatch.setattr(
        "gateway.meta_router_executor.format_routed_response",
        lambda final_response, phase2, directive="": final_response,
    )
    monkeypatch.setattr("run_agent.AIAgent._persist_session", lambda *args, **kwargs: None)
    monkeypatch.setattr("run_agent.AIAgent._save_trajectory", lambda *args, **kwargs: None)
    monkeypatch.setattr("run_agent.AIAgent._cleanup_task_resources", lambda *args, **kwargs: None)

    class _Choice:
        def __init__(self):
            self.message = SimpleNamespace(content="review safe response", tool_calls=None, refusal=None, reasoning_content=None)
            self.finish_reason = "stop"

    response = SimpleNamespace(
        choices=[_Choice()],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        model="test-model",
        id="resp-review-filter",
    )

    def _capture_api(_self, api_kwargs, *args, **kwargs):
        captured["tools"] = [tool["function"]["name"] for tool in api_kwargs.get("tools", [])]
        return response

    monkeypatch.setattr("run_agent.AIAgent._interruptible_streaming_api_call", _capture_api)
    monkeypatch.setattr("run_agent.AIAgent._interruptible_api_call", _capture_api)

    agent = AIAgent(
        model="test/model",
        base_url="https://api.openai.com/v1",
        api_key="test-key",
        api_mode="chat_completions",
        quiet_mode=True,
        skip_memory=True,
        skip_context_files=True,
    )
    original_tools = {tool["function"]["name"] for tool in agent.tools}
    agent.client = None

    result = agent.run_conversation(
        user_message="Review the helper design and explain whether it is safe.",
        conversation_history=[],
    )

    assert result["final_response"] == "review safe response"
    assert "read_file" in captured["tools"]
    assert "search_files" in captured["tools"]
    assert "session_search" in captured["tools"]
    assert "write_file" not in captured["tools"]
    assert "patch" not in captured["tools"]
    assert "terminal" not in captured["tools"]
    assert "execute_code" not in captured["tools"]
    assert "delegate_task" not in captured["tools"]
    assert "write_file" in original_tools
    assert "write_file" in {tool["function"]["name"] for tool in agent.tools}


@patch("run_agent.AIAgent._build_system_prompt", return_value="system prompt")
def test_prefixed_review_directive_filters_mutating_tools(mock_sys, monkeypatch):
    from run_agent import AIAgent

    captured = {}

    monkeypatch.setattr("run_agent.AIAgent._persist_session", lambda *args, **kwargs: None)
    monkeypatch.setattr("run_agent.AIAgent._save_trajectory", lambda *args, **kwargs: None)
    monkeypatch.setattr("run_agent.AIAgent._cleanup_task_resources", lambda *args, **kwargs: None)

    class _Choice:
        def __init__(self):
            self.message = SimpleNamespace(content="prefixed review response", tool_calls=None, refusal=None, reasoning_content=None)
            self.finish_reason = "stop"

    response = SimpleNamespace(
        choices=[_Choice()],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        model="test-model",
        id="resp-prefixed-review",
    )

    def _capture_api(_self, api_kwargs, *args, **kwargs):
        captured["tools"] = [tool["function"]["name"] for tool in api_kwargs.get("tools", [])]
        return response

    monkeypatch.setattr("run_agent.AIAgent._interruptible_streaming_api_call", _capture_api)
    monkeypatch.setattr("run_agent.AIAgent._interruptible_api_call", _capture_api)

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

    prefixed_message = (
        "[META-ROUTER | code | review]\n\n"
        "[SoM Targets | code]\n1. Functional Correctness\n[/SoM Targets]\n\n"
        "Review the helper design only."
    )
    result = agent.run_conversation(user_message=prefixed_message, conversation_history=[])

    assert result["final_response"] == "prefixed review response"
    assert "write_file" not in captured["tools"]
    assert "terminal" not in captured["tools"]
    assert "execute_code" not in captured["tools"]
    assert "read_file" in captured["tools"]


def test_preferred_session_cwd_prefers_messaging_cwd_for_gateway(monkeypatch):
    import run_agent

    monkeypatch.setenv("MESSAGING_CWD", "/tmp/messaging-cwd")
    monkeypatch.setenv("TERMINAL_CWD", "/tmp/terminal-cwd")

    assert run_agent._preferred_session_cwd("telegram") == "/tmp/messaging-cwd"


def test_preferred_session_cwd_prefers_terminal_cwd_for_cli(monkeypatch):
    import run_agent

    monkeypatch.setenv("MESSAGING_CWD", "/tmp/messaging-cwd")
    monkeypatch.setenv("TERMINAL_CWD", "/tmp/terminal-cwd")

    assert run_agent._preferred_session_cwd("cli") == "/tmp/terminal-cwd"


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
    monkeypatch.setattr("run_agent.AIAgent._interruptible_api_call", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("API path should not be used")))
    monkeypatch.setattr("run_agent.AIAgent._interruptible_streaming_api_call", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("API path should not be used")))
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
    monkeypatch.setattr("run_agent.AIAgent._interruptible_api_call", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("API path should not be used")))
    monkeypatch.setattr("run_agent.AIAgent._interruptible_streaming_api_call", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("API path should not be used")))

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
    assert "CORRECTION PASS 1/2" in handoff_prompts[1]
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
    run_outcome_calls = []
    monkeypatch.setattr(
        "gateway.meta_router_executor.run_outcome_only",
        lambda *args, **kwargs: run_outcome_calls.append((args, kwargs)),
    )
    monkeypatch.setattr("run_agent.AIAgent._persist_session", lambda *args, **kwargs: None)
    monkeypatch.setattr("run_agent.AIAgent._save_trajectory", lambda *args, **kwargs: None)
    monkeypatch.setattr("run_agent.AIAgent._cleanup_task_resources", lambda *args, **kwargs: None)
    monkeypatch.setattr("run_agent.AIAgent._interruptible_api_call", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("API path should not be used")))
    monkeypatch.setattr("run_agent.AIAgent._interruptible_streaming_api_call", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("API path should not be used")))

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
    assert run_outcome_calls
    _, kwargs = run_outcome_calls[0]
    assert kwargs["request_id"] == "rid-normal"


@patch("run_agent.AIAgent._build_system_prompt", return_value="system prompt")
def test_run_conversation_hands_off_code_tasks_to_omx_from_config(mock_sys, monkeypatch, tmp_path):
    from run_agent import AIAgent

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    output_path = state_dir / "output.md"

    monkeypatch.delenv("HERMES_ENABLE_OMX_EXECUTOR", raising=False)
    monkeypatch.setattr(
        "hermes_cli.config.load_config",
        lambda: {
            "code_harness": {
                "enabled": True,
                "default": "omx",
                "omx": {"command": "/tmp/fake-omx"},
            }
        },
    )
    monkeypatch.setattr(
        "gateway.meta_router_runtime.make_route_decision",
        lambda **kwargs: SimpleNamespace(
            request_id="rid-omx-config",
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
        output_path.write_text("print('config-routed omx')\n", encoding="utf-8")
        Path(request["result_path"]).write_text(
            json.dumps(
                {
                    "request_id": request["request_id"],
                    "status": "completed",
                    "engine": "omx",
                    "workflow": "plain",
                    "output_path": request["output_path"],
                    "command_override": command_override,
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
            "command_override": command_override,
        }

    monkeypatch.setattr("gateway.omx_executor.execute_request", fake_execute_request)
    monkeypatch.setattr("gateway.meta_router_executor.run_phase2", lambda *args, **kwargs: _phase2_result(state_dir))
    monkeypatch.setattr(
        "gateway.meta_router_executor.format_routed_response",
        lambda final_response, phase2, directive="": final_response,
    )
    monkeypatch.setattr("run_agent.AIAgent._persist_session", lambda *args, **kwargs: None)
    monkeypatch.setattr("run_agent.AIAgent._save_trajectory", lambda *args, **kwargs: None)
    monkeypatch.setattr("run_agent.AIAgent._cleanup_task_resources", lambda *args, **kwargs: None)

    def _unexpected_api(*args, **kwargs):
        raise AssertionError("model API should not be called when OMX config handoff is active")

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

    assert agent._omx_executor_enabled() is True
    assert agent._omx_command_override() == "/tmp/fake-omx"
    assert result["completed"] is True
    assert "config-routed omx" in result["final_response"]


@patch("run_agent.AIAgent._build_system_prompt", return_value="system prompt")
def test_run_conversation_logs_outcome_when_routed_response_is_empty(mock_sys, monkeypatch, tmp_path):
    from run_agent import AIAgent

    state_dir = tmp_path / "state"
    state_dir.mkdir()

    monkeypatch.delenv("HERMES_ENABLE_OMX_EXECUTOR", raising=False)
    monkeypatch.setattr(
        "gateway.meta_router_runtime.make_route_decision",
        lambda **kwargs: SimpleNamespace(
            request_id="rid-empty",
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
    run_outcome_calls = []
    monkeypatch.setattr(
        "gateway.meta_router_executor.run_outcome_only",
        lambda *args, **kwargs: run_outcome_calls.append((args, kwargs)),
    )
    monkeypatch.setattr("run_agent.AIAgent._persist_session", lambda *args, **kwargs: None)
    monkeypatch.setattr("run_agent.AIAgent._save_trajectory", lambda *args, **kwargs: None)
    monkeypatch.setattr("run_agent.AIAgent._cleanup_task_resources", lambda *args, **kwargs: None)
    monkeypatch.setattr("run_agent.AIAgent._interruptible_api_call", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("API path should not be used")))
    monkeypatch.setattr("run_agent.AIAgent._interruptible_streaming_api_call", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("API path should not be used")))

    class _Choice:
        def __init__(self):
            self.message = SimpleNamespace(content="", tool_calls=None, refusal=None, reasoning_content=None)
            self.finish_reason = "stop"

    response = SimpleNamespace(
        choices=[_Choice()],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=0, total_tokens=10),
        model="test-model",
        id="resp-empty",
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
    agent.platform = "telegram"
    agent.session_id = "sess-empty"

    result = agent.run_conversation(
        user_message="Implement a Python CLI that parses CSV files and adds tests.",
        conversation_history=[],
    )

    assert result["final_response"] == ""
    assert run_outcome_calls, "expected routed empty response to log a terminal outcome"
    _, kwargs = run_outcome_calls[0]
    assert kwargs["session_id"] == "sess-empty"
    assert kwargs["source"] == "gateway"
    assert kwargs["surface"] == "telegram"
    assert kwargs["error"] == "missing-final-response"


@patch("run_agent.AIAgent._build_system_prompt", return_value="system prompt")
def test_run_conversation_logs_terminal_outcome_when_phase2_block_raises(mock_sys, monkeypatch, tmp_path):
    from run_agent import AIAgent

    state_dir = tmp_path / "state"
    state_dir.mkdir()

    monkeypatch.delenv("HERMES_ENABLE_OMX_EXECUTOR", raising=False)
    monkeypatch.setattr(
        "gateway.meta_router_runtime.make_route_decision",
        lambda **kwargs: SimpleNamespace(
            request_id="rid-phase2-exc",
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
        "gateway.meta_router_executor.run_phase2",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    run_outcome_calls = []
    monkeypatch.setattr(
        "gateway.meta_router_executor.run_outcome_only",
        lambda *args, **kwargs: run_outcome_calls.append((args, kwargs)),
    )
    monkeypatch.setattr("run_agent.AIAgent._persist_session", lambda *args, **kwargs: None)
    monkeypatch.setattr("run_agent.AIAgent._save_trajectory", lambda *args, **kwargs: None)
    monkeypatch.setattr("run_agent.AIAgent._cleanup_task_resources", lambda *args, **kwargs: None)
    monkeypatch.setattr("run_agent.AIAgent._interruptible_api_call", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("API path should not be used")))
    monkeypatch.setattr("run_agent.AIAgent._interruptible_streaming_api_call", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("API path should not be used")))

    class _Choice:
        def __init__(self):
            self.message = SimpleNamespace(content="normal response", tool_calls=None, refusal=None, reasoning_content=None)
            self.finish_reason = "stop"

    response = SimpleNamespace(
        choices=[_Choice()],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        model="test-model",
        id="resp-phase2-exc",
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
    agent.platform = "telegram"
    agent.session_id = "sess-phase2"

    result = agent.run_conversation(
        user_message="Implement a Python CLI that parses CSV files and adds tests.",
        conversation_history=[],
    )

    assert result["final_response"] == "normal response"
    assert run_outcome_calls, "expected phase2 exception path to log a terminal outcome"
    _, kwargs = run_outcome_calls[0]
    assert kwargs["session_id"] == "sess-phase2"
    assert kwargs["source"] == "gateway"
    assert kwargs["surface"] == "telegram"
    assert kwargs["error"] == "phase2-block-exception"
    assert "phase=phase2-exception" in kwargs["notes_extra"]
    assert any(note.startswith("phase2_exception=boom") for note in kwargs["notes_extra"])


@patch("run_agent.AIAgent._build_system_prompt", return_value="system prompt")
def test_run_conversation_omx_handoff_uses_state_dir_when_terminal_cwd_missing(mock_sys, monkeypatch, tmp_path):
    from run_agent import AIAgent

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    captured = {}

    monkeypatch.setenv("HERMES_ENABLE_OMX_EXECUTOR", "1")
    monkeypatch.delenv("TERMINAL_CWD", raising=False)
    monkeypatch.delenv("MESSAGING_CWD", raising=False)

    def fake_execute_request(request, *, workdir=None, command_override=None):
        captured['workdir'] = str(workdir)
        output_path = Path(request['output_path'])
        output_path.write_text('ok', encoding='utf-8')
        Path(request['result_path']).write_text(
            json.dumps({
                'request_id': request['request_id'],
                'status': 'completed',
                'engine': 'omx',
                'workflow': 'plain',
                'output_path': request['output_path'],
            }),
            encoding='utf-8',
        )
        return {
            'request_id': request['request_id'],
            'status': 'completed',
            'engine': 'omx',
            'workflow': 'plain',
            'output_path': request['output_path'],
        }

    monkeypatch.setattr("gateway.omx_executor.execute_request", fake_execute_request)

    agent = AIAgent(
        model="test/model",
        base_url="https://api.openai.com/v1",
        api_key="test-key",
        api_mode="chat_completions",
        quiet_mode=True,
        skip_memory=True,
        skip_context_files=True,
    )
    agent.session_id = 'rid-omx-cwd'
    agent._mr_request_id = 'rid-omx-cwd'
    agent._mr_task_type = 'code'
    agent._mr_original_task = 'Implement a tiny helper.'
    agent._mr_directive = '[META-ROUTER | code | execute]'
    agent._mr_routing_artifact_version = 'candidate-0010'
    agent._mr_som_state_dir = state_dir
    agent._mr_targets_context = '[SoM Targets | code]'
    agent._mr_context_brief_path = None

    response_text, exec_result = agent._run_omx_handoff()

    assert response_text == 'ok'
    assert exec_result['status'] == 'completed'
    assert captured['workdir'] == os.getcwd()


@patch("run_agent.AIAgent._build_system_prompt", return_value="system prompt")
def test_run_omx_handoff_preserves_route_mode_and_prefers_messaging_cwd(mock_sys, monkeypatch, tmp_path):
    from run_agent import AIAgent

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    messaging_cwd = tmp_path / "messaging-cwd"
    messaging_cwd.mkdir()
    captured = {}

    monkeypatch.delenv("TERMINAL_CWD", raising=False)
    monkeypatch.setenv("MESSAGING_CWD", str(messaging_cwd))

    def fake_execute_request(request, *, workdir=None, command_override=None):
        captured["mode"] = request["mode"]
        captured["workdir"] = str(workdir)
        output_path = Path(request["output_path"])
        output_path.write_text("ok", encoding="utf-8")
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

    agent = AIAgent(
        model="test/model",
        base_url="https://api.openai.com/v1",
        api_key="test-key",
        api_mode="chat_completions",
        quiet_mode=True,
        skip_memory=True,
        skip_context_files=True,
    )
    agent.session_id = "rid-omx-review-cwd"
    agent.platform = "telegram"
    agent._mr_request_id = "rid-omx-review-cwd"
    agent._mr_task_type = "code"
    agent._mr_mode = "review"
    agent._mr_original_task = "Review the helper plan and do not execute it."
    agent._mr_directive = "[META-ROUTER | code | review]"
    agent._mr_routing_artifact_version = "candidate-0010"
    agent._mr_som_state_dir = state_dir
    agent._mr_targets_context = "[SoM Targets | code]"
    agent._mr_context_brief_path = None

    response_text, exec_result = agent._run_omx_handoff()

    assert response_text == "ok"
    assert exec_result["status"] == "completed"
    assert captured["mode"] == "review"
    assert captured["workdir"] == str(messaging_cwd)
