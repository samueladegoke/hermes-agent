import json
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from scripts.browser_harness_ssh_bridge_canary import _canary_code
from tools.browser_harness_bridge import (
    build_bridge_plan,
    build_ssh_forward_command,
    build_windows_brave_launch_command,
    extract_cdp_websocket_url,
    fetch_cdp_version_payload,
)


class _VersionHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/json/version":
            self.send_response(404)
            self.end_headers()
            return
        payload = {
            "Browser": "Brave/1.0",
            "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/test-browser",
        }
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):  # noqa: A003 - stdlib callback name
        return


def test_build_ssh_forward_command_binds_only_loopback():
    command = build_ssh_forward_command(
        ssh_target="sam@windows-host",
        local_port=9333,
        remote_port=9222,
    )

    assert command == [
        "ssh",
        "-N",
        "-L",
        "127.0.0.1:9333:127.0.0.1:9222",
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        "ServerAliveInterval=30",
        "sam@windows-host",
    ]


@pytest.mark.parametrize("bad_bind", ["0.0.0.0", "::", "192.168.1.10"])
def test_ssh_forward_rejects_non_loopback_bind_addresses(bad_bind):
    with pytest.raises(ValueError, match="loopback"):
        build_ssh_forward_command(
            ssh_target="sam@windows-host",
            local_port=9333,
            remote_port=9222,
            local_bind=bad_bind,
        )


def test_windows_brave_command_uses_dedicated_profile_and_loopback_debugging():
    command = build_windows_brave_launch_command(remote_port=9222)

    assert "--remote-debugging-address=127.0.0.1" in command
    assert "--remote-debugging-port=9222" in command
    assert "--user-data-dir=" in command
    assert "Hermes-Brave-CDP-Profile" in command
    assert "BraveSoftware" in command
    assert "Default" not in command


def test_extract_cdp_websocket_url_rewrites_remote_loopback_to_forwarded_port():
    url = extract_cdp_websocket_url(
        {
            "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/abc",
        },
        local_port=9333,
    )

    assert url == "ws://127.0.0.1:9333/devtools/browser/abc"


def test_fetch_cdp_version_payload_reads_local_forwarded_endpoint():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _VersionHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        payload = fetch_cdp_version_payload(local_port=server.server_port, timeout_seconds=2)
    finally:
        server.shutdown()
        thread.join(timeout=5)

    assert payload["Browser"] == "Brave/1.0"
    assert payload["webSocketDebuggerUrl"].endswith("/test-browser")


def test_bridge_plan_is_non_mutating_and_browser_harness_ready():
    plan = build_bridge_plan(
        ssh_target="sam@windows-host",
        local_port=9333,
        remote_port=9222,
    )

    assert plan["starts_processes"] is False
    assert plan["browser_harness_mode"] == "ssh_bridge"
    assert plan["cdp_version_url"] == "http://127.0.0.1:9333/json/version"
    assert plan["ssh_command"][3] == "127.0.0.1:9333:127.0.0.1:9222"
    assert "BU_CDP_WS" in "\n".join(plan["next_steps"])
    assert "real Brave attach remains blocked" in "\n".join(plan["safety_notes"])


def test_windows_bridge_cli_outputs_json_plan_without_starting_tunnel():
    script = Path(__file__).resolve().parents[2] / "scripts" / "browser_harness_windows_bridge.py"
    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--ssh-target",
            "sam@windows-host",
            "--local-port",
            "9333",
            "--remote-port",
            "9222",
            "--json",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload["starts_processes"] is False
    assert payload["ssh_command"][0] == "ssh"
    assert payload["browser_harness_mode"] == "ssh_bridge"


def test_windows_bridge_cli_probe_does_not_require_ssh_target():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _VersionHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    script = Path(__file__).resolve().parents[2] / "scripts" / "browser_harness_windows_bridge.py"
    try:
        completed = subprocess.run(
            [
                sys.executable,
                str(script),
                "--probe",
                "--local-port",
                str(server.server_port),
                "--json",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert "ssh_command" not in payload
    assert payload["probe"]["BU_CDP_WS"] == f"ws://127.0.0.1:{server.server_port}/devtools/browser/test-browser"


def test_ssh_bridge_canary_code_flushes_and_exits_after_success_markers():
    code = _canary_code(screenshot=True, public_url="https://example.com/", extract_text=True)

    assert "import sys" in code
    assert "print('BRIDGE_CANARY_DONE')" in code
    assert "sys.stdout.flush()" in code
    assert "os._exit(0)" in code
    assert code.index("print('BRIDGE_CANARY_DONE')") < code.index("sys.stdout.flush()") < code.index("os._exit(0)")


def test_ssh_bridge_canary_cli_runs_with_fake_browser_harness(tmp_path):
    fake_browser_harness = tmp_path / "browser-harness"
    fake_browser_harness.write_text(
        "#!/usr/bin/env python3\n"
        "from pathlib import Path\n"
        "import os\n"
        "import sys\n"
        "code = sys.argv[sys.argv.index('-c') + 1]\n"
        "def page_info():\n"
        "    return {'url': 'about:blank', 'title': '', 'w': 100, 'h': 100}\n"
        "def capture_screenshot(path):\n"
        "    Path(path).write_bytes(b'fake-png')\n"
        "exec(code, {'page_info': page_info, 'capture_screenshot': capture_screenshot, 'os': os, 'Path': Path})\n",
        encoding="utf-8",
    )
    fake_browser_harness.chmod(0o755)
    script = Path(__file__).resolve().parents[2] / "scripts" / "browser_harness_ssh_bridge_canary.py"

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--cdp-ws",
            "ws://127.0.0.1:9333/devtools/browser/test-browser",
            "--bu-name",
            "pytest-ssh-bridge-canary",
            "--browser-harness-command",
            str(fake_browser_harness),
            "--screenshot",
            "--json",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["result"]["ok"] is True
    assert payload["result"]["mode"] == "ssh_bridge"
    assert payload["result"]["bu_name"] == "pytest-ssh-bridge-canary"
    assert payload["result"]["artifacts"]
    assert payload["cleanup_removed"] == []


def test_ssh_bridge_canary_cli_returns_json_error_when_probe_fails():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        closed_port = sock.getsockname()[1]
    script = Path(__file__).resolve().parents[2] / "scripts" / "browser_harness_ssh_bridge_canary.py"

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--probe-local-port",
            str(closed_port),
            "--json",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 1
    payload = json.loads(completed.stdout)
    assert payload["ok"] is False
    assert payload["stage"] == "derive_cdp_ws"
    assert "Connection refused" in payload["error"] or "Remote end closed" in payload["error"]
    assert "Traceback" not in completed.stderr


def test_ssh_bridge_canary_cli_supports_allowlisted_public_text_extraction(tmp_path):
    fake_browser_harness = tmp_path / "browser-harness"
    fake_browser_harness.write_text(
        "#!/usr/bin/env python3\n"
        "from pathlib import Path\n"
        "import os\n"
        "import sys\n"
        "state = {'url': 'about:blank'}\n"
        "code = sys.argv[sys.argv.index('-c') + 1]\n"
        "def page_info():\n"
        "    return {'url': state['url'], 'title': 'Example Domain', 'w': 100, 'h': 100}\n"
        "def capture_screenshot(path):\n"
        "    Path(path).write_bytes(b'fake-png')\n"
        "def new_tab(url):\n"
        "    state['url'] = url\n"
        "    print('FAKE_NEW_TAB', url)\n"
        "def goto_url(url):\n"
        "    raise AssertionError('public canary should use upstream new_tab(url), not goto_url(url)')\n"
        "def wait_for_load(timeout=15.0):\n"
        "    print('FAKE_WAIT_FOR_LOAD', timeout)\n"
        "    return True\n"
        "def js(expression):\n"
        "    if 'document.body.innerText' in expression:\n"
        "        return 'Example Domain\\nThis domain is for use in illustrative examples.'\n"
        "    return None\n"
        "exec(code, {'page_info': page_info, 'capture_screenshot': capture_screenshot, 'new_tab': new_tab, 'goto_url': goto_url, 'wait_for_load': wait_for_load, 'js': js, 'os': os, 'Path': Path})\n",
        encoding="utf-8",
    )
    fake_browser_harness.chmod(0o755)
    script = Path(__file__).resolve().parents[2] / "scripts" / "browser_harness_ssh_bridge_canary.py"

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--cdp-ws",
            "ws://127.0.0.1:9333/devtools/browser/test-browser",
            "--bu-name",
            "pytest-ssh-bridge-public-text",
            "--browser-harness-command",
            str(fake_browser_harness),
            "--public-url",
            "https://example.com/",
            "--allowed-domain",
            "example.com",
            "--extract-text",
            "--json",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["result"]["ok"] is True
    stdout = payload["result"]["stdout_redacted"]
    assert "FAKE_NEW_TAB https://example.com/" in stdout
    assert "TEXT_SNIPPET" in stdout
    assert "Example Domain" in stdout
    assert payload["allowed_domains"] == ["example.com"]


def test_ssh_bridge_canary_blocks_public_url_outside_explicit_allowlist(tmp_path):
    fake_browser_harness = tmp_path / "browser-harness"
    fake_browser_harness.write_text(
        "#!/usr/bin/env python3\n"
        "raise SystemExit('fake browser-harness should not execute when policy blocks navigation')\n",
        encoding="utf-8",
    )
    fake_browser_harness.chmod(0o755)
    script = Path(__file__).resolve().parents[2] / "scripts" / "browser_harness_ssh_bridge_canary.py"

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--cdp-ws",
            "ws://127.0.0.1:9333/devtools/browser/test-browser",
            "--browser-harness-command",
            str(fake_browser_harness),
            "--public-url",
            "https://example.com/",
            "--allowed-domain",
            "allowed.example",
            "--extract-text",
            "--json",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 1
    payload = json.loads(completed.stdout)
    assert payload["result"]["ok"] is False
    assert payload["result"]["policy"]["reason"] == "domain_not_allowed"
    assert "example.com" in payload["result"]["policy"]["warnings"]
