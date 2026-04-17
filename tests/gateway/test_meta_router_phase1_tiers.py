import json
from pathlib import Path
from types import SimpleNamespace

import gateway.meta_router_executor as executor


def test_resolve_phase1_tier_uses_standard_for_serious_code_tasks():
    tier = executor.resolve_phase1_tier(
        "Implement a Python CLI that parses CSV files and adds tests.",
        "code",
    )

    assert tier == "standard"



def test_resolve_phase1_tier_uses_critical_for_production_risk_tasks():
    tier = executor.resolve_phase1_tier(
        "Deploy the auth migration to production and rotate secrets.",
        "code",
    )

    assert tier == "critical"



def test_run_phase1_passes_resolved_tier_to_som_pipeline(monkeypatch, tmp_path):
    som_pipeline = tmp_path / "som_pipeline.py"
    som_pipeline.write_text("# stub\n", encoding="utf-8")

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "targets.json").write_text(
        json.dumps({"dimensions": [{"name": "Functional Correctness"}]}),
        encoding="utf-8",
    )

    calls = []

    def fake_run(args, capture_output, text, timeout):
        calls.append(args)
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"state_dir": str(state_dir)}),
            stderr="",
        )

    monkeypatch.setattr(executor, "_SOM_PIPELINE", som_pipeline)
    monkeypatch.setattr(executor.subprocess, "run", fake_run)
    monkeypatch.setattr(executor, "_scaffold_evidence", lambda *args, **kwargs: None)
    monkeypatch.setattr(executor, "_format_targets", lambda targets, mr_type: "targets")

    prep = executor.run_phase1(
        "Implement a Python CLI that parses CSV files and adds tests.",
        "code",
    )

    assert prep.phase1_ok is True
    assert calls, "expected som_pipeline to be invoked"
    tier_index = calls[0].index("--tier") + 1
    assert calls[0][tier_index] == "standard"
