"""
meta_router_runtime.py — MR-ALS Shared Route Decision Module (Phases 1 + 3)

Direct classify (no HTTP) + rich RouteDecision + event logging + artifact routing.
Used by run_agent.py (CLI source) and meta_router_server.py (API source).

Phase 1: event logging → routing_events.jsonl
Phase 3: artifact-based routing overrides via load_active_routing.py
         (no-op when active_candidate_id == "static-default")

NOTE: log_writer.py lives in a hyphenated directory (meta-router/experience)
so it must be loaded via importlib.util, not a regular package import.
"""
from __future__ import annotations

import importlib.util
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# ── Absolute paths ─────────────────────────────────────────────────────────
_MR_DIR = Path("/home/samade10/.openclaw/workspace/skills/maintainer/meta-router")
_LOG_WRITER_PATH = _MR_DIR / "experience/log_writer.py"
_LOAD_ROUTING_PATH = _MR_DIR / "scripts/load_active_routing.py"

# ── Lazy-init event logger ──────────────────────────────────────────────────
_ALS_LOGGING = False
_log_event_fn = None
_make_request_id_fn = None


def _init_logger() -> None:
    global _ALS_LOGGING, _log_event_fn, _make_request_id_fn
    if _ALS_LOGGING:
        return
    if not _LOG_WRITER_PATH.exists():
        return
    try:
        spec = importlib.util.spec_from_file_location("_mr_log_writer", _LOG_WRITER_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _log_event_fn = mod.log_routing_event
        _make_request_id_fn = mod.make_request_id
        _ALS_LOGGING = True
    except Exception:
        pass


# ── Type → pipeline mapping ─────────────────────────────────────────────────
_PRIMARY: dict[str, str] = {
    "code":        "som",
    "audit":       "eop-adv-pass",
    "research":    "som",
    "production":  "som",
    "integration": "som",
    "design":      "som",
    "config":      "som",
}
_SECONDARY: dict[str, Optional[str]] = {
    "code":        "eop-adv-pass",
    "audit":       "som",
    "research":    None,
    "production":  "eop-adv-pass",
    "integration": "eop-adv-pass",
    "design":      None,
    "config":      None,
}
_BUDGET: dict[str, float] = {
    "code": 1.0, "audit": 1.5, "research": 1.0,
    "production": 1.5, "integration": 1.0, "design": 1.0, "config": 1.0,
}


@dataclass
class RouteDecision:
    request_id: str
    type: str
    mode: str
    directive: str
    confidence: float
    primary: str
    secondary: Optional[str]
    budget_multiplier: float
    bypassed: bool = False
    bypass_reason: str = ""


def make_route_decision(
    text: str,
    source: str = "cli",
    surface: str = "cli",
    session_id: Optional[str] = None,
) -> RouteDecision:
    """
    Classify text locally (no HTTP) and return a full RouteDecision.
    Logs the routing event to routing_events.jsonl. Never raises.
    """
    _init_logger()
    rid = _make_request_id_fn() if _make_request_id_fn else str(uuid.uuid4())

    # Phase 3: load adaptive routing overrides (no-op when static-default)
    _artifact_version = "static-default"
    _type_priority_override: list[str] = []
    try:
        if _LOAD_ROUTING_PATH.exists():
            spec3 = importlib.util.spec_from_file_location("_mr_load_routing", _LOAD_ROUTING_PATH)
            lar_mod = importlib.util.module_from_spec(spec3)
            spec3.loader.exec_module(lar_mod)
            overrides = lar_mod.load_overrides()
            _artifact_version = overrides.get("candidate_id", "static-default") or "static-default"
            _type_priority_override = overrides.get("type_priority", [])
    except Exception:
        pass

    # Direct classify — no HTTP round-trip
    try:
        from gateway.meta_router import classify as _classify
        result = _classify(text)
        decision = RouteDecision(
            request_id=rid,
            type=result.type,
            mode=result.mode,
            directive=result.directive,
            confidence=result.confidence,
            primary=_PRIMARY.get(result.type, "som"),
            secondary=_SECONDARY.get(result.type),
            budget_multiplier=_BUDGET.get(result.type, 1.0),
        )
    except Exception as e:
        decision = RouteDecision(
            request_id=rid,
            type="research",
            mode="execute",
            directive="[META-ROUTER | research | execute]",
            confidence=0.5,
            primary="som",
            secondary=None,
            budget_multiplier=1.0,
            bypassed=True,
            bypass_reason=f"classify failed: {e}",
        )

    # Log routing event (non-blocking, never raises)
    if _ALS_LOGGING and _log_event_fn:
        try:
            _log_event_fn(
                source=source,
                surface=surface,
                task_text=text,
                task_type=decision.type,
                mode=decision.mode,
                confidence=decision.confidence,
                bypassed=decision.bypassed,
                bypass_reason=decision.bypass_reason or None,
                session_id=session_id,
                request_id=rid,
                routing_artifact_version=_artifact_version,
            )
        except Exception:
            pass

    return decision
