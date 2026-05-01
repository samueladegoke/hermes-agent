import json
import os
import stat
import sys
from pathlib import Path

import pytest

from tools.browser_harness_policy import evaluate_browser_harness_request
from tools.browser_harness_tool import BROWSER_HARNESS_RUN_SCHEMA
from tools.browser_harness_runtime import (
    cleanup_daemon_artifacts,
    redact_sensitive,
    run_browser_harness,
)


def test_policy_blocks_real_brave_without_explicit_approval():
    verdict = evaluate_browser_harness_request(
        mode="real_brave",
        code="print(page_info())",
        allow_real_browser_attach=False,
    )

    assert verdict.allowed is False
    assert verdict.reason == "real_browser_attach_requires_explicit_approval"


def test_policy_blocks_credential_storage_extraction_even_with_cdp_mode():
    verdict = evaluate_browser_harness_request(
        mode="cdp",
        code="print(document.cookie); localStorage.getItem('token')",
        allow_real_browser_attach=False,
    )

    assert verdict.allowed is False
    assert verdict.reason == "sensitive_browser_storage_access_blocked"


def test_policy_blocks_page_goto_outside_domain_allowlist():
    verdict = evaluate_browser_harness_request(
        mode="cdp",
        code="goto_url('https://evil.example/login')",
        allow_real_browser_attach=False,
        allowed_domains=("example.com",),
    )

    assert verdict.allowed is False
    assert verdict.reason == "domain_not_allowed"
    assert verdict.warnings == ("evil.example",)


def test_policy_blocks_new_tab_outside_domain_allowlist():
    verdict = evaluate_browser_harness_request(
        mode="cdp",
        code="new_tab('https://evil.example/login')",
        allow_real_browser_attach=False,
        allowed_domains=("example.com",),
    )

    assert verdict.allowed is False
    assert verdict.reason == "domain_not_allowed"
    assert verdict.warnings == ("evil.example",)


def test_policy_allows_page_goto_to_allowlisted_subdomain():
    verdict = evaluate_browser_harness_request(
        mode="cdp",
        code="page.goto('https://sports.example.com/odds')",
        allow_real_browser_attach=False,
        allowed_domains=("example.com",),
    )

    assert verdict.allowed is True


def test_policy_allows_ssh_bridge_mode_without_real_attach_approval():
    verdict = evaluate_browser_harness_request(
        mode="ssh_bridge",
        code="print(page_info())",
        allow_real_browser_attach=False,
    )

    assert verdict.allowed is True
    assert verdict.mode == "ssh_bridge"


@pytest.mark.parametrize(
    ("code", "reason"),
    [
        ("page.locator('input[type=password]').fill('secret')", "sensitive_login_surface_requires_explicit_approval"),
        ("page.locator('input[type=file]').set_input_files('/tmp/id.png')", "file_transfer_requires_explicit_approval"),
        ("page.click('text=Download statement')", "file_transfer_requires_explicit_approval"),
        ("page.click('button:has-text(\"Buy now\")')", "mutating_browser_action_requires_explicit_approval"),
    ],
)
def test_policy_blocks_sensitive_or_state_changing_browser_actions(code, reason):
    verdict = evaluate_browser_harness_request(
        mode="cdp",
        code=code,
        allow_real_browser_attach=False,
    )

    assert verdict.allowed is False
    assert verdict.reason == reason


def test_redact_sensitive_masks_credentials_and_browser_tokens():
    text = """
    Authorization: Bearer secret-token-123
    Cookie: sessionid=abc123; csrftoken=def456
    BROWSER_USE_API_KEY=bu_live_secret
    websocket ws://127.0.0.1:1234/devtools/browser/not-secret
    """

    redacted = redact_sensitive(text)

    assert "secret-token-123" not in redacted
    assert "sessionid=abc123" not in redacted
    assert "bu_live_secret" not in redacted
    assert "ws://127.0.0.1:1234/devtools/browser/not-secret" in redacted
    assert "[REDACTED]" in redacted


def test_run_browser_harness_passes_isolated_bu_name_and_cdp_ws_to_command(tmp_path):
    fake = tmp_path / "browser-harness-fake"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "print(json.dumps({\n"
        "  'argv': sys.argv,\n"
        "  'BU_NAME': os.environ.get('BU_NAME'),\n"
        "  'BU_CDP_WS': os.environ.get('BU_CDP_WS'),\n"
        "  'BH_ARTIFACT_DIR': os.environ.get('BH_ARTIFACT_DIR'),\n"
        "}))\n",
        encoding="utf-8",
    )
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)

    result = run_browser_harness(
        code="print('ok')",
        mode="cdp",
        cdp_ws="ws://127.0.0.1:9222/devtools/browser/test",
        bu_name="hermes-test-bh",
        timeout_seconds=5,
        command=str(fake),
        artifact_dir=tmp_path / "artifacts",
    )

    assert result.ok is True
    assert result.exit_code == 0
    payload = json.loads(result.stdout_redacted)
    assert payload["argv"][1:] == ["-c", "print('ok')"]
    assert payload["BU_NAME"] == "hermes-test-bh"
    assert payload["BU_CDP_WS"] == "ws://127.0.0.1:9222/devtools/browser/test"
    assert payload["BH_ARTIFACT_DIR"] == str(tmp_path / "artifacts")


def test_run_browser_harness_requires_cdp_ws_for_ssh_bridge_mode(tmp_path):
    fake = tmp_path / "browser-harness-fake"
    fake.write_text("#!/usr/bin/env python3\nprint('should not run')\n", encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)

    result = run_browser_harness(
        code="print(page_info())",
        mode="ssh_bridge",
        bu_name="hermes-test-ssh-bridge",
        timeout_seconds=5,
        command=str(fake),
        artifact_dir=tmp_path / "artifacts",
    )

    assert result.ok is False
    assert result.exit_code is None
    assert result.mode == "ssh_bridge"
    assert "cdp_ws is required" in result.stderr_redacted


def test_tool_schema_exposes_ssh_bridge_mode():
    mode_schema = BROWSER_HARNESS_RUN_SCHEMA["function"]["parameters"]["properties"]["mode"]

    assert "ssh_bridge" in mode_schema["enum"]


def test_run_browser_harness_reports_missing_explicit_command_without_traceback(tmp_path):
    missing = tmp_path / "missing-browser-harness"

    result = run_browser_harness(
        code="print('ok')",
        mode="cdp",
        cdp_ws="ws://127.0.0.1:9222/devtools/browser/test",
        bu_name="hermes-test-missing-command",
        timeout_seconds=5,
        command=str(missing),
        artifact_dir=tmp_path / "artifacts",
    )

    assert result.ok is False
    assert result.exit_code is None
    assert result.timed_out is False
    assert "command not found" in result.stderr_redacted.lower()
    assert str(missing) in result.stderr_redacted


def test_run_browser_harness_reports_screenshot_artifacts_from_artifact_dir(tmp_path):
    fake = tmp_path / "browser-harness-screenshot"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import os, pathlib\n"
        "artifact_dir = pathlib.Path(os.environ['BH_ARTIFACT_DIR'])\n"
        "artifact_dir.mkdir(parents=True, exist_ok=True)\n"
        "(artifact_dir / 'page.png').write_bytes(b'PNG')\n"
        "print('screenshot saved')\n",
        encoding="utf-8",
    )
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    artifact_dir = tmp_path / "artifacts"

    result = run_browser_harness(
        code="screenshot('page.png')",
        mode="cdp",
        cdp_ws="ws://127.0.0.1:9222/devtools/browser/test",
        bu_name="hermes-test-screenshot",
        timeout_seconds=5,
        command=str(fake),
        artifact_dir=artifact_dir,
    )

    assert result.ok is True
    assert result.artifacts == (str(artifact_dir / "page.png"),)
    assert (artifact_dir / "page.png").read_bytes() == b"PNG"


def test_run_browser_harness_times_out_and_reports_clean_failure(tmp_path):
    fake = tmp_path / "browser-harness-slow"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import time\n"
        "time.sleep(2)\n",
        encoding="utf-8",
    )
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)

    result = run_browser_harness(
        code="print('slow')",
        mode="cdp",
        cdp_ws="ws://127.0.0.1:9222/devtools/browser/test",
        bu_name="hermes-test-timeout",
        timeout_seconds=0.1,
        command=str(fake),
        artifact_dir=tmp_path / "artifacts",
    )

    assert result.ok is False
    assert result.timed_out is True
    assert result.exit_code is None
    assert "timed out" in result.stderr_redacted.lower()


def test_cleanup_daemon_artifacts_removes_socket_pid_and_optional_log(tmp_path):
    bu_name = "hermes-test-cleanup"
    paths = [Path(f"/tmp/bu-{bu_name}.{suffix}") for suffix in ("sock", "pid", "log")]
    for path in paths:
        path.write_text("test", encoding="utf-8")

    removed = cleanup_daemon_artifacts(bu_name, remove_log=True)

    assert set(removed) == {str(path) for path in paths}
    assert all(not path.exists() for path in paths)
