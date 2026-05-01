import json
from pathlib import Path
from types import SimpleNamespace

import gateway.omx_executor as omx



def test_build_execution_request_sets_default_paths(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    request = omx.build_execution_request(
        request_id="rid-123",
        task_type="code",
        mode="execute",
        directive="[META-ROUTER | code | execute]",
        routing_artifact_version="candidate-0010",
        session_id="sess-1",
        state_dir=state_dir,
        targets_context="[SoM Targets | code]",
    )

    assert request["request_id"] == "rid-123"
    assert request["state_dir"] == str(state_dir)
    assert request["output_path"] == str(state_dir / "output.md")
    assert request["result_path"] == str(state_dir / "execution_result.json")
    assert request["executor_policy"]["engine"] == "omx"
    assert request["executor_policy"]["workflow"] == "plain"



def test_write_execution_request_persists_json(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    path = omx.write_execution_request(
        omx.build_execution_request(
            request_id="rid-456",
            task_type="code",
            mode="execute",
            directive="[META-ROUTER | code | execute]",
            routing_artifact_version="candidate-0010",
            session_id="sess-2",
            state_dir=state_dir,
            targets_context="targets",
        )
    )

    assert path == state_dir / "execution_request.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["request_id"] == "rid-456"



def test_render_omx_prompt_references_contract_files(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    request = omx.build_execution_request(
        request_id="rid-789",
        task_type="code",
        mode="execute",
        directive="[META-ROUTER | code | execute]",
        routing_artifact_version="candidate-0010",
        session_id="sess-3",
        state_dir=state_dir,
        targets_context="[SoM Targets | code]",
    )
    request_path = omx.write_execution_request(request)

    prompt = omx.render_omx_prompt(request, request_path=request_path)

    assert str(request_path) in prompt
    assert str(state_dir / "output.md") in prompt
    assert str(state_dir / "execution_result.json") in prompt
    assert "workflow" in prompt.lower()
    assert "Exact files changed" in prompt
    assert "Live verification" in prompt
    assert "Edge cases considered" in prompt
    assert "null/empty inputs" in prompt
    assert "type hints" in prompt
    assert "len(" in prompt
    assert "fallback" in prompt
    assert "unsupported absolute claims" in prompt



def test_resolve_omx_command_prefers_installed_binary(monkeypatch):
    monkeypatch.setattr(
        omx,
        "shutil",
        SimpleNamespace(which=lambda name: "/usr/local/bin/omx" if name == "omx" else None),
        raising=False,
    )

    cmd = omx.resolve_omx_command()

    assert cmd == ["/usr/local/bin/omx"]



def test_build_omx_exec_command_falls_back_to_npx_exec_when_binary_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(
        omx,
        "shutil",
        SimpleNamespace(which=lambda name: None),
        raising=False,
    )
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    request = omx.build_execution_request(
        request_id="rid-999",
        task_type="code",
        mode="execute",
        directive="[META-ROUTER | code | execute]",
        routing_artifact_version="candidate-0010",
        session_id="sess-4",
        state_dir=state_dir,
        targets_context="[SoM Targets | code]",
    )

    cmd = omx.build_omx_exec_command(request)

    assert cmd[:4] == ["npx", "-y", "oh-my-codex", "exec"]
    assert "--sandbox" in cmd
    assert "workspace-write" in cmd
    assert "--add-dir" in cmd
    assert str(state_dir) in cmd
    assert "--skip-git-repo-check" in cmd
    assert cmd.index("--sandbox") > cmd.index("exec")
    assert any("execution_result.json" in part or "execution_request.json" in part for part in cmd)



def test_execute_request_returns_structured_result_when_executor_writes_result(monkeypatch, tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    request = omx.build_execution_request(
        request_id="rid-exec-1",
        task_type="code",
        mode="execute",
        directive="[META-ROUTER | code | execute]",
        routing_artifact_version="candidate-0010",
        session_id="sess-5",
        state_dir=state_dir,
        targets_context="[SoM Targets | code]",
        task_text="Implement safe_divide with tests.",
    )

    def fake_run(cmd, capture_output, text, timeout, cwd):
        Path(request["output_path"]).write_text("def safe_divide(a, b):\n    ...\n", encoding="utf-8")
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
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(omx.subprocess, "run", fake_run)

    result = omx.execute_request(request, workdir=tmp_path)

    assert result["status"] == "completed"
    assert result["output_path"] == request["output_path"]
    assert (state_dir / "execution_request.json").exists()



def test_execute_request_fails_closed_when_executor_returns_without_artifacts(monkeypatch, tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    request = omx.build_execution_request(
        request_id="rid-exec-2",
        task_type="code",
        mode="execute",
        directive="[META-ROUTER | code | execute]",
        routing_artifact_version="candidate-0010",
        session_id="sess-6",
        state_dir=state_dir,
        targets_context="[SoM Targets | code]",
    )

    monkeypatch.setattr(
        omx.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="ok", stderr=""),
    )

    result = omx.execute_request(request, workdir=tmp_path)

    assert result["status"] == "failed"
    assert result["error"] == "omx execution did not produce output artifacts"
    # The fall-through path must still drop an execution_result.json so that
    # meta_router_executor.py can read executor_engine="omx" instead of null.
    persisted_path = Path(request["result_path"])
    assert persisted_path.exists()
    persisted = json.loads(persisted_path.read_text(encoding="utf-8"))
    assert persisted["engine"] == "omx"
    assert persisted["status"] == "failed"
    assert persisted["workflow"] == "plain"



def test_execute_request_surfaces_auth_failure_when_codex_returns_401(monkeypatch, tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    request = omx.build_execution_request(
        request_id="rid-exec-auth",
        task_type="code",
        mode="execute",
        directive="[META-ROUTER | code | execute]",
        routing_artifact_version="candidate-0010",
        session_id="sess-auth",
        state_dir=state_dir,
        targets_context="[SoM Targets | code]",
    )

    stderr = (
        "ERROR: unexpected status 401 Unauthorized: Missing bearer or basic authentication in header\n"
        "Could not parse your authentication token. Please try signing in again."
    )
    monkeypatch.setattr(
        omx.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="", stderr=stderr),
    )

    result = omx.execute_request(request, workdir=tmp_path)

    assert result["status"] == "failed"
    assert result["auth_failed"] is True
    assert "authentication" in result["error"].lower()



def test_execute_request_reports_unavailable_executor_when_command_missing(monkeypatch, tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    request = omx.build_execution_request(
        request_id="rid-exec-missing",
        task_type="code",
        mode="execute",
        directive="[META-ROUTER | code | execute]",
        routing_artifact_version="candidate-0010",
        session_id="sess-missing",
        state_dir=state_dir,
        targets_context="[SoM Targets | code]",
    )

    def fake_run(*args, **kwargs):
        raise FileNotFoundError("omx")

    monkeypatch.setattr(omx.subprocess, "run", fake_run)

    result = omx.execute_request(request, workdir=tmp_path)

    assert result["status"] == "failed"
    assert result["executor_unavailable"] is True
    assert "not installed" in result["error"].lower()
