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
import re
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


# ── Shared bypass contract ───────────────────────────────────────────────────
_ACK_BYPASS = {"yes", "no", "ok"}


def get_bypass_reason(text: str) -> str:
    """Return a stable bypass reason string, or "" when routing should proceed."""
    trimmed = (text or "").strip()
    lowered = trimmed.lower()

    if not trimmed:
        return "empty"
    if re.fullmatch(r"/\S+", trimmed):
        return "command"
    if trimmed[:1] in {"!", "#"}:
        return "shellish"
    if re.fullmatch(r"\d+", trimmed):
        return "numeric-only"
    if lowered in _ACK_BYPASS:
        return "short-ack"
    return ""


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
    routing_artifact_version: str = "static-default"
    bypassed: bool = False
    bypass_reason: str = ""


# ── Artifact-aware weighted classifier ────────────────────────────────────────

def _classify_with_overrides(text: str, overrides: dict, _RULES, _MODE_RULES) -> tuple[str, str, float]:
    """
    Apply artifact keyword_weight_adjustments + type_priority to a raw classify.
    Returns (task_type, mode, confidence).
    Falls back to "code" / 0.5 when all scores are zero.
    """
    weight_adj = overrides.get("keyword_weight_adjustments", {})
    type_priority = overrides.get("type_priority", [])
    min_conf = overrides.get("confidence_thresholds", {}).get("min_confidence_to_route", 0.0)
    lower = text.lower()

    # Raw keyword scores
    raw: dict[str, int] = {cat: 0 for cat, _ in _RULES}
    for category, patterns in _RULES:
        for pattern in patterns:
            if re.search(pattern, lower):
                raw[category] += 1

    # Apply weight multipliers
    adjusted: dict[str, float] = {
        cat: score * weight_adj.get(cat, {}).get("weight_multiplier", 1.0)
        for cat, score in raw.items()
    }

    # Best type with priority tie-breaking
    def _sort_key(c: str):
        pri = type_priority.index(c) if c in type_priority else 999
        return (adjusted.get(c, 0.0), -pri)

    best_type = max(adjusted, key=_sort_key) if adjusted else "code"
    best_score = adjusted.get(best_type, 0.0)

    if best_score <= 0:
        best_type = "code"
        confidence = 0.5
    else:
        total = sum(adjusted.values()) or 1.0
        confidence = round(best_score / total, 3)

    # Confidence threshold gate
    if confidence < min_conf:
        best_type = "code"
        confidence = 0.5

    # Mode inference
    mode = "execute"
    for m, patterns in _MODE_RULES[:-1]:
        if any(re.search(p, lower) for p in patterns):
            mode = m
            break

    return best_type, mode, confidence


def make_route_decision(
    text: str,
    source: str = "cli",
    surface: str = "cli",
    session_id: Optional[str] = None,
) -> RouteDecision:
    """
    Classify text locally (no HTTP) and return a full RouteDecision.
    When an active artifact is deployed (Phase 3), applies its keyword weight
    adjustments and type priority to the classification.
    Logs the routing event to routing_events.jsonl. Never raises.
    """
    _init_logger()
    rid = _make_request_id_fn() if _make_request_id_fn else str(uuid.uuid4())

    # Phase 3: load adaptive routing overrides
    _artifact_version = "static-default"
    _overrides: dict = {}
    try:
        if _LOAD_ROUTING_PATH.exists():
            spec3 = importlib.util.spec_from_file_location("_mr_load_routing", _LOAD_ROUTING_PATH)
            lar_mod = importlib.util.module_from_spec(spec3)
            spec3.loader.exec_module(lar_mod)
            _overrides = lar_mod.load_overrides()
            _artifact_version = _overrides.get("candidate_id", "static-default") or "static-default"
    except Exception:
        pass

    bypass_reason = get_bypass_reason(text)
    if bypass_reason:
        decision = RouteDecision(
            request_id=rid,
            type="code",
            mode="execute",
            directive="",
            confidence=0.0,
            primary=_PRIMARY["code"],
            secondary=_SECONDARY["code"],
            budget_multiplier=_BUDGET["code"],
            routing_artifact_version=_artifact_version,
            bypassed=True,
            bypass_reason=bypass_reason,
        )
    else:
        # Classify — with artifact overrides when an artifact is active,
        # or fall back to the base classify() when running static-default.
        try:
            from gateway.meta_router import _RULES, _MODE_RULES  # type: ignore[attr-defined]

            if _artifact_version != "static-default" and _overrides:
                task_type, mode, confidence = _classify_with_overrides(text, _overrides, _RULES, _MODE_RULES)
                # Rebuild directive in the same format as meta_router.classify()
                directive = f"[META-ROUTER | {task_type} | {mode}]"
            else:
                from gateway.meta_router import classify as _classify
                result = _classify(text)
                task_type = result.type
                mode = result.mode
                confidence = result.confidence
                directive = result.directive

            decision = RouteDecision(
                request_id=rid,
                type=task_type,
                mode=mode,
                directive=directive,
                confidence=confidence,
                primary=_PRIMARY.get(task_type, "som"),
                secondary=_SECONDARY.get(task_type),
                budget_multiplier=_BUDGET.get(task_type, 1.0),
                routing_artifact_version=_artifact_version,
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
                routing_artifact_version=_artifact_version,
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
                active_candidate_id=_artifact_version,
            )
        except Exception:
            pass

    return decision
