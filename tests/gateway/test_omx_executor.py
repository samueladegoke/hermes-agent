import json
import subprocess
from pathlib import Path

from gateway import omx_executor


def test_execute_request_salvages_timeout_when_output_artifacts_exist(monkeypatch, tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    request = omx_executor.build_execution_request(
        request_id="rid-timeout",
        task_type="code",
        mode="execute",
        directive="[META-ROUTER | code | execute]",
        routing_artifact_version="candidate-0010",
        session_id="sid-timeout",
        state_dir=state_dir,
        targets_context="[SoM Targets | code]",
        task_text="Write a tiny helper.",
    )

    output_path = Path(request["output_path"])
    result_path = Path(request["result_path"])

    def fake_run(cmd, capture_output=True, text=True, timeout=600, cwd=None, env=None):
        output_path.write_text("tiny helper", encoding="utf-8")
        result_path.write_text(json.dumps({
            "request_id": request["request_id"],
            "status": "completed",
            "engine": "omx",
            "workflow": "plain",
            "output_path": request["output_path"],
        }), encoding="utf-8")
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout, output="", stderr="")

    monkeypatch.setattr(omx_executor, "write_execution_request", lambda req: Path(req["request_path"]))
    monkeypatch.setattr(omx_executor, "build_omx_exec_command", lambda req, request_path=None, command_override=None, workdir=None: ["omx", "exec", "noop"])
    monkeypatch.setattr(omx_executor.subprocess, "run", fake_run)
    monkeypatch.setattr(omx_executor, "_tool_version", lambda name, args=("--version",): f"{name}-version")

    result = omx_executor.execute_request(request, workdir=state_dir)

    assert result["status"] == "completed"
    assert result["timed_out"] is True
    stored = json.loads(result_path.read_text(encoding="utf-8"))
    assert stored["status"] == "completed"
    assert stored["timed_out"] is True



def test_build_omx_exec_command_includes_explicit_cd_workdir(tmp_path):
    request = omx_executor.build_execution_request(
        request_id="rid-cmd",
        task_type="code",
        mode="execute",
        directive="[META-ROUTER | code | execute]",
        routing_artifact_version="candidate-0010",
        session_id="sid-cmd",
        state_dir=tmp_path / "state",
        targets_context="[SoM Targets | code]",
        task_text="Write a tiny helper.",
    )

    cmd = omx_executor.build_omx_exec_command(request, workdir=tmp_path / "state")

    assert "-C" in cmd
    idx = cmd.index("-C")
    assert cmd[idx + 1] == str(tmp_path / "state")


def test_execute_request_clears_stale_artifacts_before_new_attempt(monkeypatch, tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    request = omx_executor.build_execution_request(
        request_id="rid-stale",
        task_type="code",
        mode="execute",
        directive="[META-ROUTER | code | execute]",
        routing_artifact_version="candidate-0010",
        session_id="sid-stale",
        state_dir=state_dir,
        targets_context="[SoM Targets | code]",
        task_text="Write a tiny helper.",
    )
    Path(request["output_path"]).write_text("old answer", encoding="utf-8")
    Path(request["result_path"]).write_text(json.dumps({"status": "completed"}), encoding="utf-8")

    def fake_run(cmd, capture_output=True, text=True, timeout=600, cwd=None, env=None):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="new attempt failed")

    monkeypatch.setattr(omx_executor, "build_omx_exec_command", lambda req, request_path=None, command_override=None, workdir=None: ["omx", "exec", "noop"])
    monkeypatch.setattr(omx_executor.subprocess, "run", fake_run)
    monkeypatch.setattr(omx_executor, "_tool_version", lambda name, args=("--version",): f"{name}-version")

    result = omx_executor.execute_request(request, workdir=state_dir)

    assert result["status"] == "failed"
    assert result["error"] == "omx execution did not produce output artifacts"
    assert not Path(request["output_path"]).exists()


def test_execute_request_rejects_completed_metadata_without_output(monkeypatch, tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    request = omx_executor.build_execution_request(
        request_id="rid-metadata-only",
        task_type="code",
        mode="execute",
        directive="[META-ROUTER | code | execute]",
        routing_artifact_version="candidate-0010",
        session_id="sid-metadata-only",
        state_dir=state_dir,
        targets_context="[SoM Targets | code]",
        task_text="Write a tiny helper.",
    )

    def fake_run(cmd, capture_output=True, text=True, timeout=600, cwd=None, env=None):
        Path(request["result_path"]).write_text(
            json.dumps({"status": "completed", "engine": "omx", "output_path": request["output_path"]}),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(omx_executor, "build_omx_exec_command", lambda req, request_path=None, command_override=None, workdir=None: ["omx", "exec", "noop"])
    monkeypatch.setattr(omx_executor.subprocess, "run", fake_run)
    monkeypatch.setattr(omx_executor, "_tool_version", lambda name, args=("--version",): f"{name}-version")

    result = omx_executor.execute_request(request, workdir=state_dir)

    assert result["status"] == "failed"
    assert result["error"] == "omx completed metadata without output artifact"


def test_execute_request_handles_invalid_timeout_and_missing_workdir(monkeypatch, tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    request = omx_executor.build_execution_request(
        request_id="rid-timeout-policy",
        task_type="code",
        mode="execute",
        directive="[META-ROUTER | code | execute]",
        routing_artifact_version="candidate-0010",
        session_id="sid-timeout-policy",
        state_dir=state_dir,
        targets_context="[SoM Targets | code]",
        task_text="Write a tiny helper.",
        executor_policy={"timeout_seconds": "not-a-number"},
    )

    seen = {}

    def fake_build(req, request_path=None, command_override=None, workdir=None):
        seen["workdir"] = workdir
        return ["omx", "exec", "noop"]

    def fake_run(cmd, capture_output=True, text=True, timeout=600, cwd=None, env=None):
        seen["timeout"] = timeout
        seen["cwd"] = cwd
        Path(request["output_path"]).write_text("new answer", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(omx_executor, "build_omx_exec_command", fake_build)
    monkeypatch.setattr(omx_executor.subprocess, "run", fake_run)
    monkeypatch.setattr(omx_executor, "_tool_version", lambda name, args=("--version",): f"{name}-version")

    result = omx_executor.execute_request(request, workdir=tmp_path / "missing")

    assert result["status"] == "completed"
    assert seen["timeout"] == omx_executor.DEFAULT_TIMEOUT_SECONDS
    assert seen["cwd"] == str(state_dir)
    assert seen["workdir"] == str(state_dir)
