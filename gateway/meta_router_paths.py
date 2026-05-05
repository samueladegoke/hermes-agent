"""Path resolution for Hermes' imported OpenClaw MR-ALS/RQL runtime assets."""
from __future__ import annotations

import os
from pathlib import Path

_HERMES_REPO = Path(__file__).resolve().parents[1]
_LEGACY_OPENCLAW_WORKSPACE = Path("/home/samade10/.openclaw/workspace")
_WORKSPACE_ENV_NAMES = ("HERMES_OPENCLAW_WORKSPACE", "OPENCLAW_WORKSPACE", "MR_ALS_WORKSPACE")
_META_ROUTER_ENV_NAMES = ("HERMES_META_ROUTER_PATH", "MR_ALS_META_ROUTER_DIR", "META_ROUTER_DIR")
_RQL_ENV_NAMES = ("HERMES_RQL_DIR", "MR_ALS_RQL_DIR", "RQL_ROOT")


def _env_path(*names: str) -> Path | None:
    for name in names:
        value = os.environ.get(name)
        if value and value.strip():
            return Path(value).expanduser()
    return None


def _hermes_home() -> Path:
    configured = os.environ.get("HERMES_HOME")
    if configured and configured.strip():
        return Path(configured).expanduser()
    return Path.home() / ".hermes"


def _workspace_candidates() -> list[Path]:
    return [
        _hermes_home() / "workspace" / "openclaw-workspace",
        _HERMES_REPO / "openclaw-workspace",
        _LEGACY_OPENCLAW_WORKSPACE,
    ]


def openclaw_workspace() -> Path:
    """Return the OpenClaw-derived runtime workspace Hermes should use.

    The Hermes-local import is preferred because the original OpenClaw workspace
    can be archived or replaced during OpenClaw upgrades. Environment variables
    remain the explicit override for emergency rollbacks and test isolation.
    """
    configured = _env_path(*_WORKSPACE_ENV_NAMES)
    if configured is not None:
        return configured
    if os.environ.get("HERMES_HOME"):
        return _hermes_home() / "workspace" / "openclaw-workspace"

    candidates = _workspace_candidates()
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def meta_router_dir() -> Path:
    configured = _env_path(*_META_ROUTER_ENV_NAMES)
    if configured is not None:
        return configured
    return openclaw_workspace() / "skills" / "maintainer" / "meta-router"


def rql_dir() -> Path:
    configured = _env_path(*_RQL_ENV_NAMES)
    if configured is not None:
        return configured
    return openclaw_workspace() / "rql"


def rql_scripts_dir() -> Path:
    return rql_dir() / "scripts"


def rql_state_dir() -> Path:
    return rql_dir() / "state"
