import json
from pathlib import Path

from gateway.meta_router_executor import (
    Phase2Result,
    format_routed_response,
    populate_evidence_artifacts,
    resolve_phase2_tier,
)


def test_resolve_phase2_tier_prefers_state_manifest(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps({"tier": "trivial"}))
    (tmp_path / "targets.json").write_text(json.dumps({"tier": "standard"}))

    assert resolve_phase2_tier(tmp_path) == "trivial"


def test_populate_evidence_artifacts_sets_hypothesis_and_evidence(tmp_path):
    (tmp_path / "hypothesis.json").write_text(json.dumps({"task": "", "hypothesis": "", "test_plan": [], "result": "pending", "notes": "", "generated_at": ""}))
    (tmp_path / "verification_evidence.json").write_text(json.dumps({"items": []}))
    (tmp_path / "edge_scan.json").write_text(json.dumps({"categories_checked": [], "findings": []}))

    populate_evidence_artifacts(
        "Create a tiny Python add(a, b) function",
        "Verified at runtime:\n- add(2, 3) == 5\n- docstring present",
        tmp_path,
    )

    hypothesis = json.loads((tmp_path / "hypothesis.json").read_text())
    evidence = json.loads((tmp_path / "verification_evidence.json").read_text())

    assert hypothesis["hypothesis"]
    assert hypothesis["result"] == "supported"
    assert evidence["items"]


def test_format_routed_response_success_includes_receipt():
    result = Phase2Result(
        request_id="rid-1",
        task_type="code",
        state_dir=Path("/tmp/state-1"),
        routing_artifact_version="candidate-0002",
        passed=True,
        score=91.0,
        verdict="GOOD",
        threshold=90,
        oracle_verdict="PASS",
        adv_pass_clean=True,
        adv_findings_count=0,
        delivery_gate_passed=True,
        score_card="RQL Score: 91/100 (GOOD)",
        ref_entry={"id": "ref-123", "score": 91, "verdict": "GOOD"},
        delivery_path="/tmp/state-1/delivery.json",
        fix_prompt_path=None,
        error=None,
        notes=["artifact=candidate-0002"],
    )

    text = format_routed_response(
        "def add(a, b):\n    return a + b",
        result,
        directive="[META-ROUTER | code | execute]",
    )

    assert "def add(a, b)" in text
    assert "Oracle: PASS" in text
    assert "Artifact: candidate-0002" in text
    assert "REF: ref-123" in text
    assert "State dir: /tmp/state-1" in text


def test_format_routed_response_failure_blocks_unqualified_success():
    result = Phase2Result(
        request_id="rid-2",
        task_type="code",
        state_dir=Path("/tmp/state-2"),
        routing_artifact_version="candidate-0002",
        passed=False,
        score=64.0,
        verdict="ACCEPTABLE",
        threshold=90,
        oracle_verdict="FAIL",
        adv_pass_clean=False,
        adv_findings_count=2,
        delivery_gate_passed=False,
        score_card=None,
        ref_entry=None,
        delivery_path=None,
        fix_prompt_path="/tmp/state-2/fix_prompt.md",
        error=None,
        notes=["som_status=needs_fix"],
    )

    text = format_routed_response(
        "Here you go:\n\ndef multiply(a, b):\n    return a * b",
        result,
        directive="[META-ROUTER | code | execute]",
    )

    assert "Backend evaluation failed" in text
    assert "Fix prompt: /tmp/state-2/fix_prompt.md" in text
    assert "Oracle: FAIL" in text
    assert "ADV_PASS: FAIL (2 findings)" in text
    assert not text.startswith("Here you go")



def test_format_routed_response_below_threshold_explains_score_gate():
    result = Phase2Result(
        request_id="rid-3",
        task_type="code",
        state_dir=Path("/tmp/state-3"),
        routing_artifact_version="candidate-0002",
        passed=False,
        score=64.0,
        verdict="ACCEPTABLE",
        threshold=65,
        oracle_verdict="PASS",
        adv_pass_clean=True,
        adv_findings_count=0,
        delivery_gate_passed=False,
        score_card=None,
        ref_entry=None,
        delivery_path="/tmp/state-3/delivery.json",
        fix_prompt_path=None,
        error=None,
        notes=["som_status=complete"],
    )

    text = format_routed_response(
        "def multiply(a, b):\n    return a * b",
        result,
        directive="[META-ROUTER | code | execute]",
    )

    assert "missed the final score threshold" in text
    assert "Score gate: FAIL (64.0 < 65.0)" in text
    assert "Oracle: PASS" in text
    assert "ADV_PASS: PASS (0 findings)" in text
