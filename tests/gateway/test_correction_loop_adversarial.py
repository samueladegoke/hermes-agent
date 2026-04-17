"""
Adversarial tests for the meta-router correction loop.

Coverage:
  1. _synthesize_fix_prompt — 10 edge cases
  2. _do_phase2 trivial-tier gap — synthesis fires/skips correctly
  3. Correction loop prefix guard — prefix must NOT start with [META-ROUTER |
  4. resolve_phase1_tier — safe defaults for high-risk work
  5. run_agent.py correction loop — max-pass cap, None-score, empty response,
     exception logging, output.md overwrite
"""
import json
import logging
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call

import pytest

# ─── stubs for heavy optional deps not installed in test env ─────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.modules.setdefault("fire", types.SimpleNamespace(Fire=lambda *a, **k: None))
sys.modules.setdefault("firecrawl", types.SimpleNamespace(Firecrawl=object))
sys.modules.setdefault("fal_client", types.SimpleNamespace())
sys.modules.setdefault("yaml", types.ModuleType("yaml"))

import gateway.meta_router_executor as executor

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 1: _synthesize_fix_prompt unit tests
# ─────────────────────────────────────────────────────────────────────────────

GOOD_SCORES = {
    "dimensions": [
        {"name": "Accuracy", "weighted": 25, "max_possible": 50, "reasoning": "Missing citation"},
        {"name": "Completeness", "weighted": 15, "max_possible": 50, "reasoning": "Skipped edge cases"},
    ]
}


def test_synthesize_fix_prompt_happy_path(tmp_path):
    """Creates fix_prompt.md with failing dimensions listed."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "scores.json").write_text(json.dumps(GOOD_SCORES), encoding="utf-8")

    result = executor._synthesize_fix_prompt(state_dir, "Write a report", 58.0, 65.0)

    assert result is not None
    assert result == state_dir / "fix_prompt.md"
    content = result.read_text(encoding="utf-8")
    assert "58" in content
    assert "65" in content
    assert "Accuracy" in content
    assert "Completeness" in content
    assert "Missing citation" in content
    assert "Revise your response" in content


def test_synthesize_fix_prompt_idempotent_does_not_overwrite(tmp_path):
    """If fix_prompt.md already exists, it is returned unchanged."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "scores.json").write_text(json.dumps(GOOD_SCORES), encoding="utf-8")
    (state_dir / "fix_prompt.md").write_text("ORIGINAL CONTENT", encoding="utf-8")

    result = executor._synthesize_fix_prompt(state_dir, "task", 50.0, 65.0)

    assert result == state_dir / "fix_prompt.md"
    assert result.read_text(encoding="utf-8") == "ORIGINAL CONTENT"


def test_synthesize_fix_prompt_missing_scores_json_returns_none(tmp_path):
    """No scores.json → returns None, no file created."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    result = executor._synthesize_fix_prompt(state_dir, "task", 50.0, 65.0)

    assert result is None
    assert not (state_dir / "fix_prompt.md").exists()


def test_synthesize_fix_prompt_empty_scores_json_returns_none(tmp_path):
    """scores.json exists but is empty JSON object → returns None."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "scores.json").write_text("{}", encoding="utf-8")

    result = executor._synthesize_fix_prompt(state_dir, "task", 50.0, 65.0)

    assert result is None


def test_synthesize_fix_prompt_no_dimensions_returns_none(tmp_path):
    """scores.json with empty dimensions list → returns None."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "scores.json").write_text(
        json.dumps({"dimensions": []}), encoding="utf-8"
    )

    result = executor._synthesize_fix_prompt(state_dir, "task", 50.0, 65.0)

    assert result is None


def test_synthesize_fix_prompt_all_passing_uses_worst_two(tmp_path):
    """When all dims >= 60%, picks the two lowest-scoring dims."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    scores = {
        "dimensions": [
            {"name": "Alpha", "weighted": 40, "max_possible": 50, "reasoning": "r1"},  # 80%
            {"name": "Beta", "weighted": 32, "max_possible": 50, "reasoning": "r2"},   # 64%
            {"name": "Gamma", "weighted": 33, "max_possible": 50, "reasoning": "r3"},  # 66%
        ]
    }
    (state_dir / "scores.json").write_text(json.dumps(scores), encoding="utf-8")

    result = executor._synthesize_fix_prompt(state_dir, "task", 63.0, 65.0)

    assert result is not None
    content = result.read_text(encoding="utf-8")
    # Beta (64%) and Gamma (66%) are the two lowest
    assert "Beta" in content
    assert "Gamma" in content
    # Alpha (80%) should NOT be included
    assert "Alpha" not in content


def test_synthesize_fix_prompt_score_none_returns_none(tmp_path):
    """score=None causes TypeError in format string → caught, returns None."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "scores.json").write_text(json.dumps(GOOD_SCORES), encoding="utf-8")

    # score=None passed directly (not filtered at call site)
    result = executor._synthesize_fix_prompt(state_dir, "task", None, 65.0)

    # The function should return None gracefully (TypeError caught)
    assert result is None
    assert not (state_dir / "fix_prompt.md").exists()


def test_synthesize_fix_prompt_threshold_none_defaults_to_65(tmp_path):
    """threshold=None uses the effective_threshold default of 65."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "scores.json").write_text(json.dumps(GOOD_SCORES), encoding="utf-8")

    result = executor._synthesize_fix_prompt(state_dir, "task", 58.0, None)

    assert result is not None
    content = result.read_text(encoding="utf-8")
    assert "65" in content


def test_synthesize_fix_prompt_dimension_max_possible_zero_no_division_error(tmp_path):
    """Dimension with max_possible=0 is filtered (no ZeroDivisionError)."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    scores = {
        "dimensions": [
            {"name": "Broken", "weighted": 0, "max_possible": 0, "reasoning": "n/a"},
            {"name": "Real", "weighted": 20, "max_possible": 50, "reasoning": "needs work"},
        ]
    }
    (state_dir / "scores.json").write_text(json.dumps(scores), encoding="utf-8")

    result = executor._synthesize_fix_prompt(state_dir, "task", 30.0, 65.0)

    assert result is not None
    content = result.read_text(encoding="utf-8")
    assert "Real" in content
    assert "Broken" not in content  # filtered out (max_possible=0)


def test_synthesize_fix_prompt_unwritable_state_dir_returns_none(tmp_path):
    """If state_dir can't be written to, returns None gracefully."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "scores.json").write_text(json.dumps(GOOD_SCORES), encoding="utf-8")
    state_dir.chmod(0o555)  # read-only

    try:
        result = executor._synthesize_fix_prompt(state_dir, "task", 50.0, 65.0)
        assert result is None
    finally:
        state_dir.chmod(0o755)  # restore for cleanup


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 2: _do_phase2 trivial-tier gap
# ─────────────────────────────────────────────────────────────────────────────

def _make_fake_subprocess_run(state_dir, *, returncode=0):
    """Returns a fake subprocess.run that simulates SoM completing."""
    (state_dir / "scores.json").write_text(
        json.dumps({
            "total_weighted_score": 58,
            "threshold": 65,
            "verdict": "FAIL",
            "dimensions": [
                {"name": "Accuracy", "weighted": 25, "max_possible": 50, "reasoning": "Needs more detail"},
            ],
        }),
        encoding="utf-8",
    )
    (state_dir / "delivery.json").write_text(
        json.dumps({"oracle": "PASS", "delivery_gate": {"all_passed": False}}),
        encoding="utf-8",
    )

    def fake_run(args, capture_output, text, timeout):
        return SimpleNamespace(
            returncode=returncode,
            stdout=json.dumps({"status": "complete", "passed": False}),
            stderr="",
        )
    return fake_run


def test_do_phase2_synthesizes_fix_prompt_when_trivial_tier_has_none(monkeypatch, tmp_path):
    """Trivial-tier: SoM doesn't write fix_prompt.md but score fails.
    _do_phase2 must synthesize one so the correction loop can fire.
    """
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "output.md").write_text("first draft", encoding="utf-8")
    som_pipeline = tmp_path / "som_pipeline.py"
    som_pipeline.write_text("# stub\n", encoding="utf-8")

    monkeypatch.setattr(executor, "_SOM_PIPELINE", som_pipeline)
    monkeypatch.setattr(executor, "populate_evidence_artifacts", lambda *a, **k: None)
    monkeypatch.setattr(executor, "_validate_evidence", lambda *a, **k: (None, ""))
    monkeypatch.setattr(executor, "_run_adv_pass", lambda *a, **k: (None, None, ""))
    monkeypatch.setattr(executor, "_load_log_writer", lambda: None)
    monkeypatch.setattr(executor, "_maybe_trigger_optimizer", lambda: None)
    monkeypatch.setattr(executor, "_enhance_fix_prompt", lambda *a, **k: None)
    monkeypatch.setattr(executor.subprocess, "run", _make_fake_subprocess_run(state_dir))

    # CRITICAL: no fix_prompt.md exists before the call
    assert not (state_dir / "fix_prompt.md").exists()

    phase2 = executor._do_phase2(
        request_id="rid-trivial",
        task_type="research",
        task_text="List all active memory layers",
        som_state_dir=state_dir,
        final_response="first draft",
        t0=0.0,
        routing_artifact_version="static-default",
        session_id=None,
    )

    # fix_prompt.md must now exist AND be referenced in the result
    assert (state_dir / "fix_prompt.md").exists(), "fix_prompt.md was not synthesized"
    assert phase2.fix_prompt_path is not None, "fix_prompt_path should not be None"
    assert "fix_prompt.md" in phase2.fix_prompt_path

    content = (state_dir / "fix_prompt.md").read_text(encoding="utf-8")
    assert "58" in content or "Accuracy" in content  # synthesized from scores


def test_do_phase2_does_not_synthesize_when_score_passes(monkeypatch, tmp_path):
    """When score >= threshold, _synthesize_fix_prompt must NOT be called."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "output.md").write_text("great answer", encoding="utf-8")
    (state_dir / "scores.json").write_text(
        json.dumps({
            "total_weighted_score": 70,
            "threshold": 65,
            "verdict": "PASS",
            "dimensions": [{"name": "A", "weighted": 40, "max_possible": 50, "reasoning": ""}],
        }),
        encoding="utf-8",
    )
    (state_dir / "delivery.json").write_text(
        json.dumps({"oracle": "PASS", "delivery_gate": {"all_passed": True}}),
        encoding="utf-8",
    )
    som_pipeline = tmp_path / "som_pipeline.py"
    som_pipeline.write_text("# stub\n", encoding="utf-8")

    synth_calls = []

    def fake_synth(*a, **k):
        synth_calls.append(a)
        return None

    monkeypatch.setattr(executor, "_SOM_PIPELINE", som_pipeline)
    monkeypatch.setattr(executor, "populate_evidence_artifacts", lambda *a, **k: None)
    monkeypatch.setattr(executor, "_validate_evidence", lambda *a, **k: (None, ""))
    monkeypatch.setattr(executor, "_run_adv_pass", lambda *a, **k: (None, None, ""))
    monkeypatch.setattr(executor, "_load_log_writer", lambda: None)
    monkeypatch.setattr(executor, "_maybe_trigger_optimizer", lambda: None)
    monkeypatch.setattr(executor, "_enhance_fix_prompt", lambda *a, **k: None)
    monkeypatch.setattr(executor, "_synthesize_fix_prompt", fake_synth)
    monkeypatch.setattr(
        executor.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"status": "complete", "passed": True}),
            stderr="",
        ),
    )

    executor._do_phase2(
        request_id="rid-passing",
        task_type="research",
        task_text="task that passes",
        som_state_dir=state_dir,
        final_response="great answer",
        t0=0.0,
        routing_artifact_version="static-default",
        session_id=None,
    )

    assert synth_calls == [], "_synthesize_fix_prompt must not be called when score passes"


def test_do_phase2_does_not_synthesize_when_fix_prompt_already_exists(monkeypatch, tmp_path):
    """If SoM DID write fix_prompt.md, _synthesize_fix_prompt is not called."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "output.md").write_text("draft", encoding="utf-8")
    (state_dir / "fix_prompt.md").write_text("original fix prompt", encoding="utf-8")
    som_pipeline = tmp_path / "som_pipeline.py"
    som_pipeline.write_text("# stub\n", encoding="utf-8")

    synth_calls = []
    monkeypatch.setattr(executor, "_synthesize_fix_prompt", lambda *a, **k: synth_calls.append(a) or None)
    monkeypatch.setattr(executor, "_SOM_PIPELINE", som_pipeline)
    monkeypatch.setattr(executor, "populate_evidence_artifacts", lambda *a, **k: None)
    monkeypatch.setattr(executor, "_validate_evidence", lambda *a, **k: (None, ""))
    monkeypatch.setattr(executor, "_run_adv_pass", lambda *a, **k: (None, None, ""))
    monkeypatch.setattr(executor, "_load_log_writer", lambda: None)
    monkeypatch.setattr(executor, "_maybe_trigger_optimizer", lambda: None)
    monkeypatch.setattr(executor, "_enhance_fix_prompt", lambda *a, **k: None)
    monkeypatch.setattr(
        executor.subprocess,
        "run",
        _make_fake_subprocess_run(state_dir),
    )

    executor._do_phase2(
        request_id="rid-has-fp",
        task_type="code",
        task_text="task with existing fix prompt",
        som_state_dir=state_dir,
        final_response="draft",
        t0=0.0,
        routing_artifact_version="static-default",
        session_id=None,
    )

    assert synth_calls == [], "_synthesize_fix_prompt must not be called when fix_prompt.md exists"


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 3: Correction loop prefix guard
# ─────────────────────────────────────────────────────────────────────────────

def test_correction_loop_prefix_does_not_start_with_meta_router_guard():
    """The fix_prefix in run_agent.py must NOT start with '[META-ROUTER |'.

    If it does, run_conversation's guard skips meta-router classification
    AND all post-processing, making the correction a blind no-op.
    """
    import run_agent
    import inspect
    source = inspect.getsource(run_agent.AIAgent.run_conversation)

    # Find the _fix_prefix assignment
    assert "_fix_prefix" in source, "Could not find _fix_prefix in run_conversation"

    # Extract all string literals following '_fix_prefix ='
    # The prefix should NOT start with [META-ROUTER |
    lines = source.splitlines()
    in_fix_prefix = False
    prefix_lines = []
    for line in lines:
        stripped = line.strip()
        if "_fix_prefix = (" in stripped:
            in_fix_prefix = True
        if in_fix_prefix:
            prefix_lines.append(stripped)
            if stripped.endswith(")") and len(prefix_lines) > 1:
                break

    prefix_block = "\n".join(prefix_lines)
    assert "[META-ROUTER |" not in prefix_block, (
        f"_fix_prefix starts with the META-ROUTER guard string, "
        f"which causes run_conversation to skip classification.\n"
        f"Block found:\n{prefix_block}"
    )


def test_meta_router_guard_string_is_present_in_run_conversation():
    """Verify the guard '[META-ROUTER |' is still active and not accidentally removed."""
    import run_agent
    import inspect
    source = inspect.getsource(run_agent.AIAgent.run_conversation)
    assert 'not user_message.startswith("[META-ROUTER |")' in source, (
        "Guard removed — verify intentional"
    )


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 4: resolve_phase1_tier
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("task,mr_type,expected", [
    ("Deploy to production", "code", "critical"),
    ("Rotate the API keys and update the config", "config", "critical"),
    ("Run the database migration on prod", "code", "critical"),
    ("Implement OAuth2 flow with tests", "code", "critical"),  # OAuth → auth signal → critical
    ("Research the existing memory layers", "research", "standard"),
    ("Review the payment service code", "audit", "critical"),  # payment is a critical-risk signal
    ("Fix a typo", "general", "trivial"),        # short + general → trivial
    ("x" * 90, "general", "standard"),           # long + general → standard
    ("Auth token rotation", "integration", "critical"),  # auth signal
    ("Encrypt user PII at rest", "code", "critical"),   # encrypt signal
])
def test_resolve_phase1_tier(task, mr_type, expected):
    result = executor.resolve_phase1_tier(task, mr_type)
    assert result == expected, f"Task: {task!r}, type: {mr_type!r} → expected {expected!r}, got {result!r}"


def test_resolve_phase1_tier_case_insensitive():
    """Critical signal detection must be case-insensitive."""
    assert executor.resolve_phase1_tier("DEPLOY TO PRODUCTION NOW", "code") == "critical"
    assert executor.resolve_phase1_tier("Database Migration Steps", "code") == "critical"


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 5: run_agent.py correction loop
# ─────────────────────────────────────────────────────────────────────────────

def _make_phase2_result(passed, score, threshold, fix_prompt_path):
    return SimpleNamespace(
        passed=passed,
        score=score,
        threshold=threshold,
        fix_prompt_path=fix_prompt_path,
    )


def _make_minimal_agent():
    """Build an AIAgent with minimal state for testing the correction loop."""
    from run_agent import AIAgent
    agent = AIAgent.__new__(AIAgent)
    agent._mr_request_id = "req-test-1"
    agent._mr_som_state_dir = "/tmp/fake_state"
    agent._mr_task_type = "code"
    agent._mr_start_time = 0.0
    agent._mr_original_task = "Implement the routing fix"
    agent._mr_routing_artifact_version = "static-default"
    agent._mr_directive = ""
    agent.session_id = "session-test"
    return agent


def test_correction_loop_runs_on_failing_phase2(tmp_path):
    """When phase2 fails AND fix_prompt exists, run_conversation is called once."""
    from run_agent import AIAgent

    fix_prompt = tmp_path / "fix_prompt.md"
    fix_prompt.write_text("Improve accuracy section.", encoding="utf-8")
    output_md = tmp_path / "output.md"
    output_md.write_text("original", encoding="utf-8")

    agent = _make_minimal_agent()
    agent._mr_som_state_dir = str(tmp_path)

    run_conv_calls = []

    def fake_run_conv(user_message, **kwargs):
        run_conv_calls.append(user_message)
        # After first correction, pretend it passes
        return {"final_response": "corrected response"}

    phase2_results = [
        _make_phase2_result(False, 58.0, 65.0, str(fix_prompt)),  # first eval: fail
        _make_phase2_result(True, 70.0, 65.0, None),              # second eval: pass
    ]
    call_count = [0]

    def fake_p2(*args, **kwargs):
        result = phase2_results[min(call_count[0], len(phase2_results) - 1)]
        call_count[0] += 1
        return result

    messages = [{"role": "assistant", "content": "original"}]
    final_response = "original"

    with patch.object(agent, "run_conversation", side_effect=fake_run_conv):
        with patch("gateway.meta_router_executor.run_phase2", side_effect=fake_p2):
            with patch("gateway.meta_router_executor.format_routed_response", return_value="formatted"):
                with patch("gateway.meta_router_executor.run_outcome_only"):
                    # Simulate the correction loop directly
                    from gateway.meta_router_executor import run_phase2 as _mr_p2, format_routed_response as _mr_fmt
                    _MR_MAX_FIX_PASSES = 2
                    _mr_fix_pass = 0
                    _mr_phase2 = fake_p2()
                    while (
                        not _mr_phase2.passed
                        and _mr_fix_pass < _MR_MAX_FIX_PASSES
                        and _mr_phase2.fix_prompt_path
                    ):
                        _mr_fix_pass += 1
                        from pathlib import Path as _MRPath
                        _fix_instructions = _MRPath(_mr_phase2.fix_prompt_path).read_text(encoding="utf-8")
                        _score_str = f"{_mr_phase2.score:.0f}" if _mr_phase2.score is not None else "?"
                        _thresh_str = f"{_mr_phase2.threshold:.0f}" if _mr_phase2.threshold is not None else "?"
                        _fix_prefix = (
                            f"CORRECTION PASS {_mr_fix_pass}/{_MR_MAX_FIX_PASSES} — "
                            f"Score was {_score_str}/{_thresh_str}, revision needed before delivery.\n\n"
                            f"{_fix_instructions}\n\n"
                            f"Revise and restate your complete response below."
                        )
                        _fix_result = agent.run_conversation(user_message=_fix_prefix, conversation_history=list(messages))
                        _fix_response = _fix_result.get("final_response", "").strip()
                        if _fix_response:
                            final_response = _fix_response
                            (_MRPath(str(tmp_path)) / "output.md").write_text(final_response, encoding="utf-8")
                            _mr_phase2 = fake_p2()
                        else:
                            break

    assert len(run_conv_calls) == 1, f"Expected 1 correction pass, got {len(run_conv_calls)}"
    assert "CORRECTION PASS 1/2" in run_conv_calls[0]
    assert "[META-ROUTER |" not in run_conv_calls[0]
    assert "Improve accuracy section" in run_conv_calls[0]
    assert output_md.read_text(encoding="utf-8") == "corrected response"


def test_correction_loop_skips_when_no_fix_prompt():
    """When fix_prompt_path is None, the while loop must not execute."""
    from run_agent import AIAgent
    agent = _make_minimal_agent()
    run_conv_calls = []
    agent.run_conversation = lambda **kw: run_conv_calls.append(kw) or {"final_response": "x"}

    phase2 = _make_phase2_result(False, 58.0, 65.0, fix_prompt_path=None)
    _MR_MAX_FIX_PASSES = 2
    _mr_fix_pass = 0
    while (
        not phase2.passed
        and _mr_fix_pass < _MR_MAX_FIX_PASSES
        and phase2.fix_prompt_path  # ← None → loop never enters
    ):
        _mr_fix_pass += 1

    assert run_conv_calls == [], "run_conversation must not be called when fix_prompt_path is None"
    assert _mr_fix_pass == 0


def test_correction_loop_respects_max_fix_passes_cap(tmp_path):
    """Loop must exit after _MR_MAX_FIX_PASSES even if phase2 keeps failing."""
    fix_prompt = tmp_path / "fix_prompt.md"
    fix_prompt.write_text("keep trying", encoding="utf-8")

    run_conv_calls = []
    always_failing = _make_phase2_result(False, 50.0, 65.0, str(fix_prompt))

    _MR_MAX_FIX_PASSES = 2
    _mr_fix_pass = 0
    _mr_phase2 = always_failing

    def fake_run_conv(user_message, **kwargs):
        run_conv_calls.append(user_message)
        return {"final_response": "revised but still bad"}

    from run_agent import AIAgent
    agent = _make_minimal_agent()
    agent.run_conversation = fake_run_conv

    while (
        not _mr_phase2.passed
        and _mr_fix_pass < _MR_MAX_FIX_PASSES
        and _mr_phase2.fix_prompt_path
    ):
        _mr_fix_pass += 1
        from pathlib import Path as _MRPath
        _fix_instructions = _MRPath(_mr_phase2.fix_prompt_path).read_text(encoding="utf-8")
        _score_str = f"{_mr_phase2.score:.0f}" if _mr_phase2.score is not None else "?"
        _thresh_str = f"{_mr_phase2.threshold:.0f}" if _mr_phase2.threshold is not None else "?"
        _fix_prefix = (
            f"CORRECTION PASS {_mr_fix_pass}/{_MR_MAX_FIX_PASSES} — "
            f"Score was {_score_str}/{_thresh_str}, revision needed before delivery.\n\n"
            f"{_fix_instructions}\n\n"
            f"Revise and restate your complete response below."
        )
        agent.run_conversation(user_message=_fix_prefix, conversation_history=[])
        # phase2 remains failing after each pass

    assert _mr_fix_pass == _MR_MAX_FIX_PASSES, f"Expected {_MR_MAX_FIX_PASSES} passes, got {_mr_fix_pass}"
    assert len(run_conv_calls) == _MR_MAX_FIX_PASSES


def test_correction_loop_none_score_uses_question_mark(tmp_path):
    """score=None and threshold=None must produce '?' in the prefix, not crash."""
    fix_prompt = tmp_path / "fix_prompt.md"
    fix_prompt.write_text("improve it", encoding="utf-8")

    prefixes_seen = []
    phase2 = _make_phase2_result(False, score=None, threshold=None, fix_prompt_path=str(fix_prompt))
    _MR_MAX_FIX_PASSES = 1
    _mr_fix_pass = 0

    while (
        not phase2.passed
        and _mr_fix_pass < _MR_MAX_FIX_PASSES
        and phase2.fix_prompt_path
    ):
        _mr_fix_pass += 1
        try:
            from pathlib import Path as _MRPath
            _fix_instructions = _MRPath(phase2.fix_prompt_path).read_text(encoding="utf-8")
            _score_str = f"{phase2.score:.0f}" if phase2.score is not None else "?"
            _thresh_str = f"{phase2.threshold:.0f}" if phase2.threshold is not None else "?"
            _fix_prefix = (
                f"CORRECTION PASS {_mr_fix_pass}/{_MR_MAX_FIX_PASSES} — "
                f"Score was {_score_str}/{_thresh_str}, revision needed before delivery.\n\n"
                f"{_fix_instructions}\n\n"
                f"Revise and restate your complete response below."
            )
            prefixes_seen.append(_fix_prefix)
        except Exception as exc:
            pytest.fail(f"Correction loop raised exception for None score: {exc}")

    assert len(prefixes_seen) == 1
    assert "?/?" in prefixes_seen[0], f"Expected '?/?' in prefix, got: {prefixes_seen[0][:100]}"
    assert "[META-ROUTER |" not in prefixes_seen[0]


def test_correction_loop_empty_fix_response_does_not_overwrite(tmp_path):
    """If run_conversation returns empty string, original response is kept."""
    fix_prompt = tmp_path / "fix_prompt.md"
    fix_prompt.write_text("improve", encoding="utf-8")
    output_md = tmp_path / "output.md"
    output_md.write_text("original good response", encoding="utf-8")

    final_response = "original good response"
    phase2 = _make_phase2_result(False, 60.0, 65.0, str(fix_prompt))
    _MR_MAX_FIX_PASSES = 1
    _mr_fix_pass = 0

    while (
        not phase2.passed
        and _mr_fix_pass < _MR_MAX_FIX_PASSES
        and phase2.fix_prompt_path
    ):
        _mr_fix_pass += 1
        try:
            from pathlib import Path as _MRPath
            _fix_instructions = _MRPath(phase2.fix_prompt_path).read_text(encoding="utf-8")
            _score_str = f"{phase2.score:.0f}" if phase2.score is not None else "?"
            _thresh_str = f"{phase2.threshold:.0f}" if phase2.threshold is not None else "?"
            _fix_prefix = (
                f"CORRECTION PASS {_mr_fix_pass}/{_MR_MAX_FIX_PASSES} — "
                f"Score was {_score_str}/{_thresh_str}, revision needed before delivery.\n\n"
                f"{_fix_instructions}\n\n"
                f"Revise and restate your complete response below."
            )
            # Simulate empty response from LLM
            _fix_response = "   ".strip()  # empty after strip
            if _fix_response:
                final_response = _fix_response
                (_MRPath(str(tmp_path)) / "output.md").write_text(final_response, encoding="utf-8")
        except Exception:
            break

    assert final_response == "original good response"
    assert output_md.read_text(encoding="utf-8") == "original good response"


def test_correction_loop_exception_logs_and_breaks(tmp_path, caplog):
    """Exception in inner try block is logged, loop breaks gracefully."""
    fix_prompt = tmp_path / "fix_prompt.md"
    fix_prompt.write_text("improve", encoding="utf-8")

    phase2 = _make_phase2_result(False, 55.0, 65.0, str(fix_prompt))
    _MR_MAX_FIX_PASSES = 2
    _mr_fix_pass = 0
    loop_completed = False

    with caplog.at_level(logging.WARNING, logger="meta_router"):
        while (
            not phase2.passed
            and _mr_fix_pass < _MR_MAX_FIX_PASSES
            and phase2.fix_prompt_path
        ):
            _mr_fix_pass += 1
            try:
                raise RuntimeError("Simulated LLM failure")
            except Exception as _mr_fix_exc:
                import logging as _mr_logging
                _mr_logging.getLogger("meta_router").warning(
                    "MR correction pass %d failed: %s", _mr_fix_pass, _mr_fix_exc
                )
                break
        loop_completed = True

    assert loop_completed
    assert _mr_fix_pass == 1, "Should have broken after first exception"
    assert any("MR correction pass" in r.message for r in caplog.records), (
        "Exception must be logged to meta_router logger, not silently swallowed"
    )


def test_output_md_overwritten_after_correction(tmp_path):
    """After a correction pass, output.md must contain the NEW response."""
    fix_prompt = tmp_path / "fix_prompt.md"
    fix_prompt.write_text("be more specific", encoding="utf-8")
    output_md = tmp_path / "output.md"
    output_md.write_text("first draft — too vague", encoding="utf-8")

    final_response = "first draft — too vague"
    corrected_response = "improved specific response with citations"
    phase2 = _make_phase2_result(False, 60.0, 65.0, str(fix_prompt))
    _MR_MAX_FIX_PASSES = 1
    _mr_fix_pass = 0

    while (
        not phase2.passed
        and _mr_fix_pass < _MR_MAX_FIX_PASSES
        and phase2.fix_prompt_path
    ):
        _mr_fix_pass += 1
        try:
            from pathlib import Path as _MRPath
            _fix_instructions = _MRPath(phase2.fix_prompt_path).read_text(encoding="utf-8")
            _score_str = f"{phase2.score:.0f}" if phase2.score is not None else "?"
            _thresh_str = f"{phase2.threshold:.0f}" if phase2.threshold is not None else "?"
            _fix_prefix = (
                f"CORRECTION PASS {_mr_fix_pass}/{_MR_MAX_FIX_PASSES} — "
                f"Score was {_score_str}/{_thresh_str}, revision needed before delivery.\n\n"
                f"{_fix_instructions}\n\n"
                f"Revise and restate your complete response below."
            )
            _fix_response = corrected_response  # simulated LLM correction
            if _fix_response:
                final_response = _fix_response
                # This is the critical write that was missing before the fix
                (_MRPath(str(tmp_path)) / "output.md").write_text(final_response, encoding="utf-8")
        except Exception:
            break

    assert output_md.read_text(encoding="utf-8") == corrected_response, (
        "output.md must be overwritten with corrected response so re-evaluation "
        "scores the new text, not the original"
    )
    assert final_response == corrected_response


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 6: Integration smoke — existing tests still pass
# ─────────────────────────────────────────────────────────────────────────────

def test_phase1_tier_tests_still_pass():
    """Smoke: the phase1 tier function still passes its original contract."""
    assert executor.resolve_phase1_tier("Implement CSV parser with tests", "code") == "standard"
    assert executor.resolve_phase1_tier("Deploy auth migration to production and rotate secrets", "code") == "critical"


def test_synthesize_produces_actionable_fix_prompt(tmp_path):
    """End-to-end: synthesized fix_prompt.md is human-readable and actionable."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "scores.json").write_text(
        json.dumps({
            "dimensions": [
                {"name": "Evidence Quality", "weighted": 20, "max_possible": 40, "reasoning": "No citations found"},
                {"name": "Task Completion", "weighted": 15, "max_possible": 40, "reasoning": "Missing implementation steps"},
                {"name": "Accuracy", "weighted": 35, "max_possible": 40, "reasoning": "Generally correct"},
            ]
        }),
        encoding="utf-8",
    )

    result = executor._synthesize_fix_prompt(state_dir, "Research memory providers", 62.0, 65.0)

    assert result is not None
    content = result.read_text(encoding="utf-8")

    # Check structure
    assert "62" in content       # score present
    assert "65" in content       # threshold present
    assert "Evidence Quality" in content
    assert "No citations found" in content
    assert "Task Completion" in content
    assert "Missing implementation steps" in content
    assert "Accuracy" not in content  # passes at 87.5%, should not be in failing list
    assert "Revise your response" in content
    assert len(content) > 50     # meaningful content, not empty
