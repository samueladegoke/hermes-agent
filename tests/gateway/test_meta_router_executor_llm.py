import json
from pathlib import Path
from types import SimpleNamespace

import gateway.meta_router_executor as executor


def test_enhance_fix_prompt_overwrites_file_with_task_specific_text(monkeypatch, tmp_path):
    fix_prompt = tmp_path / "fix_prompt.md"
    fix_prompt.write_text("add edge cases", encoding="utf-8")

    monkeypatch.setattr(
        executor,
        "_call_llm_text_prompt",
        lambda instructions, prompt, timeout_seconds: "Mention the branch name and the conflict resolution steps.",
    )

    executor._enhance_fix_prompt(
        fix_prompt,
        "Resolve the merge conflict and report the branch.",
        "code",
        42.0,
        65.0,
    )

    assert fix_prompt.read_text(encoding="utf-8") == "Mention the branch name and the conflict resolution steps.\n"



def test_enhance_fix_prompt_preserves_original_when_llm_fails(monkeypatch, tmp_path):
    fix_prompt = tmp_path / "fix_prompt.md"
    fix_prompt.write_text("add edge cases", encoding="utf-8")

    monkeypatch.setattr(executor, "_call_llm_text_prompt", lambda *args, **kwargs: None)

    executor._enhance_fix_prompt(
        fix_prompt,
        "Resolve the merge conflict and report the branch.",
        "code",
        42.0,
        65.0,
    )

    assert fix_prompt.read_text(encoding="utf-8") == "add edge cases"



def test_do_phase2_enhances_fix_prompt_for_low_scores(monkeypatch, tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "output.md").write_text("draft response", encoding="utf-8")
    (state_dir / "scores.json").write_text(
        json.dumps({"total_weighted_score": 40, "threshold": 65, "verdict": "FAIL"}),
        encoding="utf-8",
    )
    (state_dir / "delivery.json").write_text(
        json.dumps({"oracle": "PASS", "delivery_gate": {"all_passed": False}}),
        encoding="utf-8",
    )
    (state_dir / "fix_prompt.md").write_text("add edge cases", encoding="utf-8")

    som_pipeline = tmp_path / "som_pipeline.py"
    som_pipeline.write_text("# stub\n", encoding="utf-8")

    calls = []

    def fake_enhance(path, task_text, task_type, score, threshold):
        calls.append((Path(path), task_text, task_type, score, threshold))

    monkeypatch.setattr(executor, "_SOM_PIPELINE", som_pipeline)
    monkeypatch.setattr(executor, "populate_evidence_artifacts", lambda *args, **kwargs: None)
    monkeypatch.setattr(executor, "_validate_evidence", lambda *args, **kwargs: (None, ""))
    monkeypatch.setattr(executor, "_run_adv_pass", lambda *args, **kwargs: (None, None, ""))
    monkeypatch.setattr(executor, "_load_log_writer", lambda: None)
    monkeypatch.setattr(executor, "_maybe_trigger_optimizer", lambda: None)
    monkeypatch.setattr(executor, "_enhance_fix_prompt", fake_enhance, raising=False)
    monkeypatch.setattr(
        executor.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"status": "complete", "passed": False}),
            stderr="",
        ),
    )

    phase2 = executor._do_phase2(
        request_id="rid-1",
        task_type="code",
        task_text="Implement the routing fix",
        som_state_dir=state_dir,
        final_response="draft response",
        t0=0.0,
        routing_artifact_version="static-default",
        session_id=None,
    )

    assert phase2.fix_prompt_path == str(state_dir / "fix_prompt.md")
    assert calls == [
        (
            state_dir / "fix_prompt.md",
            "Implement the routing fix",
            "code",
            40.0,
            65.0,
        )
    ]
