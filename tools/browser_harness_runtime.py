"""Hermes runtime wrapper for browser-use/browser-harness.

This module is intentionally small and conservative.  It invokes the pinned
`browser-harness` command only through the runtime form verified for the current
upstream commit (`browser-harness -c <code>`), enforces timeouts, assigns an
isolated `BU_NAME`, captures/redacts output, and exposes cleanup helpers for the
upstream `/tmp/bu-$BU_NAME.*` daemon artifacts.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from typing import Iterable, Mapping, Optional

from tools.browser_harness_policy import evaluate_browser_harness_request

DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_ARTIFACT_ROOT = Path.home() / ".hermes" / "browser-harness" / "artifacts"

_REDACTION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(Authorization:\s*Bearer\s+)[^\s]+", re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"(Cookie:\s*)[^\n\r]+", re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"(Set-Cookie:\s*)[^\n\r]+", re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"(X-Browser-Use-API-Key:\s*)[^\s]+", re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"(BROWSER_USE_API_KEY\s*=\s*)[^\s]+", re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"((?:api[_-]?key|secret|token|password)\s*=\s*)[^\s;&]+", re.IGNORECASE), r"\1[REDACTED]"),
)


@dataclass(frozen=True)
class BrowserHarnessRunResult:
    ok: bool
    mode: str
    bu_name: str
    command: list[str]
    exit_code: Optional[int]
    stdout_redacted: str
    stderr_redacted: str
    duration_ms: int
    timed_out: bool = False
    artifact_dir: Optional[str] = None
    artifacts: tuple[str, ...] = field(default_factory=tuple)
    policy: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "mode": self.mode,
            "bu_name": self.bu_name,
            "command": self.command,
            "exit_code": self.exit_code,
            "stdout_redacted": self.stdout_redacted,
            "stderr_redacted": self.stderr_redacted,
            "duration_ms": self.duration_ms,
            "timed_out": self.timed_out,
            "artifact_dir": self.artifact_dir,
            "artifacts": list(self.artifacts),
            "policy": self.policy,
        }


def redact_sensitive(text: str) -> str:
    """Redact common credential/session-bearing values from tool output."""
    redacted = text or ""
    for pattern, replacement in _REDACTION_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def _safe_bu_name(value: Optional[str]) -> str:
    raw = value or f"hermes-{int(time.time())}"
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", raw).strip(".-")
    return safe or f"hermes-{int(time.time())}"


def find_browser_harness_command() -> Optional[str]:
    """Find the browser-harness executable from env, PATH, or ~/.local/bin."""
    configured = os.environ.get("HERMES_BROWSER_HARNESS_COMMAND", "").strip()
    if configured:
        path = Path(configured).expanduser()
        if path.exists() and os.access(path, os.X_OK):
            return str(path)
        resolved = shutil.which(configured)
        if resolved:
            return resolved

    resolved = shutil.which("browser-harness")
    if resolved:
        return resolved

    local = Path.home() / ".local" / "bin" / "browser-harness"
    if local.exists() and os.access(local, os.X_OK):
        return str(local)
    return None


def _resolve_command(command: Optional[str]) -> Optional[str]:
    if not command:
        return find_browser_harness_command()
    expanded = Path(command).expanduser()
    has_path_separator = os.sep in command or (os.altsep is not None and os.altsep in command)
    if expanded.is_absolute() or has_path_separator:
        if expanded.exists() and os.access(expanded, os.X_OK):
            return str(expanded)
        return None
    return shutil.which(command)


def cleanup_daemon_artifacts(bu_name: str, *, remove_log: bool = False) -> list[str]:
    """Remove upstream daemon socket/pid artifacts for a BU_NAME.

    The log is retained by default for evidence/debugging.  Pass
    `remove_log=True` when the caller wants a full cleanup.
    """
    safe_name = _safe_bu_name(bu_name)
    suffixes = ["sock", "pid"] + (["log"] if remove_log else [])
    removed: list[str] = []
    for suffix in suffixes:
        path = Path(f"/tmp/bu-{safe_name}.{suffix}")
        try:
            if path.exists() or path.is_socket():
                path.unlink()
                removed.append(str(path))
        except FileNotFoundError:
            continue
    return removed


def _default_artifact_dir(bu_name: str) -> Path:
    day = time.strftime("%Y-%m-%d")
    return DEFAULT_ARTIFACT_ROOT / day / bu_name


def _list_artifacts(artifact_path: Path) -> tuple[str, ...]:
    if not artifact_path.exists():
        return ()
    return tuple(
        str(path)
        for path in sorted(artifact_path.rglob("*"))
        if path.is_file()
    )


def run_browser_harness(
    *,
    code: str = "",
    mode: str = "cdp",
    cdp_ws: Optional[str] = None,
    bu_name: Optional[str] = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    command: Optional[str] = None,
    artifact_dir: Optional[os.PathLike[str] | str] = None,
    allow_real_browser_attach: bool = False,
    allowed_domains: Optional[Iterable[str]] = None,
    extra_env: Optional[Mapping[str, str]] = None,
) -> BrowserHarnessRunResult:
    """Run browser-harness with policy checks, isolation, timeout and redaction."""
    safe_bu_name = _safe_bu_name(bu_name)
    verdict = evaluate_browser_harness_request(
        mode=mode,
        code=code,
        allow_real_browser_attach=allow_real_browser_attach,
        allowed_domains=allowed_domains,
    )
    normalized_mode = verdict.mode
    cmd_path = _resolve_command(command)
    cmd_display = [cmd_path or command or "browser-harness"]
    start = time.monotonic()

    artifact_path = Path(artifact_dir) if artifact_dir is not None else _default_artifact_dir(safe_bu_name)

    if not verdict.allowed:
        return BrowserHarnessRunResult(
            ok=False,
            mode=normalized_mode,
            bu_name=safe_bu_name,
            command=cmd_display,
            exit_code=None,
            stdout_redacted="",
            stderr_redacted=verdict.reason,
            duration_ms=int((time.monotonic() - start) * 1000),
            artifact_dir=str(artifact_path),
            policy=verdict.to_dict(),
        )

    if not cmd_path:
        return BrowserHarnessRunResult(
            ok=False,
            mode=normalized_mode,
            bu_name=safe_bu_name,
            command=cmd_display,
            exit_code=None,
            stdout_redacted="",
            stderr_redacted=f"browser-harness command not found: {cmd_display[0]}",
            duration_ms=int((time.monotonic() - start) * 1000),
            artifact_dir=str(artifact_path),
            policy=verdict.to_dict(),
        )

    if normalized_mode in {"cdp", "disposable", "dedicated_brave", "ssh_bridge"} and not cdp_ws and normalized_mode != "doctor":
        return BrowserHarnessRunResult(
            ok=False,
            mode=normalized_mode,
            bu_name=safe_bu_name,
            command=[cmd_path],
            exit_code=None,
            stdout_redacted="",
            stderr_redacted="cdp_ws is required for safe non-real-browser modes",
            duration_ms=int((time.monotonic() - start) * 1000),
            artifact_dir=str(artifact_path),
            policy=verdict.to_dict(),
        )

    artifact_path.mkdir(parents=True, exist_ok=True)

    args = [cmd_path, "--doctor"] if normalized_mode == "doctor" else [cmd_path, "-c", code]
    env = os.environ.copy()
    env["PATH"] = f"{Path.home() / '.local' / 'bin'}{os.pathsep}" + env.get("PATH", "")
    env["BU_NAME"] = safe_bu_name
    env["BH_ARTIFACT_DIR"] = str(artifact_path)
    if cdp_ws:
        env["BU_CDP_WS"] = cdp_ws
    if extra_env:
        env.update(dict(extra_env))

    try:
        completed = subprocess.run(
            args,
            env=env,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
        duration_ms = int((time.monotonic() - start) * 1000)
        return BrowserHarnessRunResult(
            ok=completed.returncode == 0,
            mode=normalized_mode,
            bu_name=safe_bu_name,
            command=args,
            exit_code=completed.returncode,
            stdout_redacted=redact_sensitive(completed.stdout),
            stderr_redacted=redact_sensitive(completed.stderr),
            duration_ms=duration_ms,
            artifact_dir=str(artifact_path),
            artifacts=_list_artifacts(artifact_path),
            policy=verdict.to_dict(),
        )
    except subprocess.TimeoutExpired as exc:
        cleanup_daemon_artifacts(safe_bu_name)
        duration_ms = int((time.monotonic() - start) * 1000)
        stdout = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode(errors="replace")
        stderr = exc.stderr if isinstance(exc.stderr, str) else (exc.stderr or b"").decode(errors="replace")
        stderr = (stderr + f"\nbrowser-harness timed out after {timeout_seconds} seconds").strip()
        return BrowserHarnessRunResult(
            ok=False,
            mode=normalized_mode,
            bu_name=safe_bu_name,
            command=args,
            exit_code=None,
            stdout_redacted=redact_sensitive(stdout),
            stderr_redacted=redact_sensitive(stderr),
            duration_ms=duration_ms,
            timed_out=True,
            artifact_dir=str(artifact_path),
            artifacts=_list_artifacts(artifact_path),
            policy=verdict.to_dict(),
        )
