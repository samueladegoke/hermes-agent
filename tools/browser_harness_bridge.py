"""Planning helpers for a Windows Brave SSH/CDP bridge.

The real Brave browser for Sam's setup lives on a Windows host, while Hermes runs
inside a headless Linux VM.  This module deliberately **does not** start Brave,
open SSH tunnels, or attach browser-harness by itself.  It produces loopback-only
commands and verifies already-forwarded CDP endpoints so real-session attach can
remain an explicit, auditable step.
"""
from __future__ import annotations

import json
from typing import Iterable, Sequence
from urllib.parse import urlsplit, urlunsplit
from urllib.request import urlopen

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
DEFAULT_LOCAL_PORT = 9333
DEFAULT_REMOTE_PORT = 9222
DEFAULT_WINDOWS_PROFILE = r"$Env:USERPROFILE\Hermes-Brave-CDP-Profile"
DEFAULT_WINDOWS_BRAVE_PATH = r"$Env:ProgramFiles\BraveSoftware\Brave-Browser\Application\brave.exe"


def _validate_port(port: int | str, *, name: str) -> int:
    try:
        value = int(port)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer TCP port") from exc
    if value < 1 or value > 65535:
        raise ValueError(f"{name} must be between 1 and 65535")
    return value


def _validate_loopback_host(host: str, *, name: str) -> str:
    normalized = (host or "").strip().lower().strip("[]")
    if normalized not in LOOPBACK_HOSTS:
        raise ValueError(f"{name} must be a loopback address, got {host!r}")
    return "127.0.0.1" if normalized == "localhost" else normalized


def _validate_ssh_target(ssh_target: str) -> str:
    target = (ssh_target or "").strip()
    if not target:
        raise ValueError("ssh_target is required")
    if any(ch in target for ch in "\r\n\x00"):
        raise ValueError("ssh_target must be a single SSH destination argument")
    return target


def build_ssh_forward_command(
    *,
    ssh_target: str,
    local_port: int = DEFAULT_LOCAL_PORT,
    remote_port: int = DEFAULT_REMOTE_PORT,
    local_bind: str = "127.0.0.1",
    remote_bind: str = "127.0.0.1",
    ssh_binary: str = "ssh",
    extra_ssh_args: Sequence[str] | None = None,
) -> list[str]:
    """Return an argv list for a loopback-only SSH CDP tunnel.

    The command intentionally binds both sides to loopback:
    `ssh -N -L 127.0.0.1:<local>:127.0.0.1:<remote> target`.
    It is returned as an argv list for auditability and safe subprocess use.
    """
    target = _validate_ssh_target(ssh_target)
    local = _validate_port(local_port, name="local_port")
    remote = _validate_port(remote_port, name="remote_port")
    local_host = _validate_loopback_host(local_bind, name="local_bind")
    remote_host = _validate_loopback_host(remote_bind, name="remote_bind")
    binary = (ssh_binary or "ssh").strip() or "ssh"
    command = [
        binary,
        "-N",
        "-L",
        f"{local_host}:{local}:{remote_host}:{remote}",
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        "ServerAliveInterval=30",
    ]
    if extra_ssh_args:
        command.extend(str(arg) for arg in extra_ssh_args)
    command.append(target)
    return command


def build_windows_brave_launch_command(
    *,
    remote_port: int = DEFAULT_REMOTE_PORT,
    brave_path: str = DEFAULT_WINDOWS_BRAVE_PATH,
    profile_dir: str = DEFAULT_WINDOWS_PROFILE,
) -> str:
    """Return a PowerShell command for a dedicated Windows Brave CDP profile."""
    port = _validate_port(remote_port, name="remote_port")
    path = (brave_path or DEFAULT_WINDOWS_BRAVE_PATH).strip()
    profile = (profile_dir or DEFAULT_WINDOWS_PROFILE).strip()
    return (
        f'& "{path}" '
        "--remote-debugging-address=127.0.0.1 "
        f"--remote-debugging-port={port} "
        f'--user-data-dir="{profile}" '
        "--no-first-run "
        "--no-default-browser-check"
    )


def cdp_version_url(*, local_port: int = DEFAULT_LOCAL_PORT, local_host: str = "127.0.0.1") -> str:
    port = _validate_port(local_port, name="local_port")
    host = _validate_loopback_host(local_host, name="local_host")
    return f"http://{host}:{port}/json/version"


def fetch_cdp_version_payload(
    *,
    local_port: int = DEFAULT_LOCAL_PORT,
    local_host: str = "127.0.0.1",
    timeout_seconds: float = 5.0,
) -> dict:
    """Fetch `/json/version` from an already-running local CDP tunnel."""
    url = cdp_version_url(local_port=local_port, local_host=local_host)
    with urlopen(url, timeout=timeout_seconds) as response:  # noqa: S310 - loopback-only URL is validated above
        body = response.read().decode("utf-8")
    payload = json.loads(body)
    if not isinstance(payload, dict):
        raise ValueError("CDP /json/version response must be a JSON object")
    return payload


def extract_cdp_websocket_url(
    payload: dict,
    *,
    local_port: int = DEFAULT_LOCAL_PORT,
    local_host: str = "127.0.0.1",
) -> str:
    """Extract and rewrite `webSocketDebuggerUrl` for the VM-side forward."""
    raw = str((payload or {}).get("webSocketDebuggerUrl") or "").strip()
    if not raw:
        raise ValueError("webSocketDebuggerUrl missing from CDP /json/version payload")
    parsed = urlsplit(raw)
    if parsed.scheme not in {"ws", "wss"}:
        raise ValueError("webSocketDebuggerUrl must use ws:// or wss://")
    remote_host = (parsed.hostname or "").lower()
    if remote_host not in LOOPBACK_HOSTS:
        raise ValueError("webSocketDebuggerUrl must point at the remote loopback CDP endpoint")
    port = _validate_port(local_port, name="local_port")
    host = _validate_loopback_host(local_host, name="local_host")
    return urlunsplit((parsed.scheme, f"{host}:{port}", parsed.path, parsed.query, parsed.fragment))


def build_bridge_plan(
    *,
    ssh_target: str,
    local_port: int = DEFAULT_LOCAL_PORT,
    remote_port: int = DEFAULT_REMOTE_PORT,
    profile_dir: str = DEFAULT_WINDOWS_PROFILE,
    brave_path: str = DEFAULT_WINDOWS_BRAVE_PATH,
    extra_ssh_args: Iterable[str] | None = None,
) -> dict:
    """Return a non-mutating bridge plan for Windows Brave -> VM CDP."""
    ssh_command = build_ssh_forward_command(
        ssh_target=ssh_target,
        local_port=local_port,
        remote_port=remote_port,
        extra_ssh_args=tuple(extra_ssh_args or ()),
    )
    windows_command = build_windows_brave_launch_command(
        remote_port=remote_port,
        brave_path=brave_path,
        profile_dir=profile_dir,
    )
    version_url = cdp_version_url(local_port=local_port)
    return {
        "starts_processes": False,
        "browser_harness_mode": "ssh_bridge",
        "local_port": _validate_port(local_port, name="local_port"),
        "remote_port": _validate_port(remote_port, name="remote_port"),
        "cdp_version_url": version_url,
        "windows_brave_command": windows_command,
        "ssh_command": ssh_command,
        "next_steps": [
            "Run the Windows Brave command on the Windows host to start a dedicated CDP profile bound to 127.0.0.1 only.",
            "Run the SSH command from the VM to forward the Windows loopback CDP port to the VM loopback port.",
            f"Fetch {version_url} and rewrite webSocketDebuggerUrl to the VM-side forwarded port as BU_CDP_WS.",
            "Call browser_harness_run with mode='ssh_bridge', explicit cdp_ws=BU_CDP_WS, and read-only code first.",
        ],
        "safety_notes": [
            "This plan does not start processes or attach browser-harness by itself.",
            "The CDP port is loopback-only on both Windows and the VM; never expose it to the LAN.",
            "Use a dedicated Brave profile first; real Brave attach remains blocked until explicit approval gates pass.",
        ],
    }
