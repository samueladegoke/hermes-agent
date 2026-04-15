"""
meta_router_executor.py — MR-ALS Pipeline Executor

Bridges RouteDecision → SoM pipeline (som_pipeline.py) deterministically.

Phase 1 (pre-LLM):  generate targets via run_pipeline() — fast, rule-based, no LLM.
Phase 2 (post-LLM): write output.md → complete_pipeline() → score + oracle + REF log.
Phase 2 runs in a background daemon thread so it never delays the response.
"""
from __future__ import annotations

import json
import re
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# ── Absolute paths ────────────────────────────────────────────────────────────
_WORKSPACE = Path("/home/samade10/.openclaw/workspace")
_PIPELINE = _WORKSPACE / "rql/scripts/som_pipeline.py"
_EXP_DIR = _WORKSPACE / "skills/maintainer/meta-router/experience"

# MR task type → SoM task type (som_pipeline.py VALID_TASK_TYPES)
_SOM_TYPE: dict[str, str] = {
    "code":        "code",
    "audit":       "general",
    "research":    "research",
    "production":  "general",
    "integration": "code",
    "design":      "design",
    "config":      "config",
}


@dataclass
class PrepResult:
    state_dir: str
    task_id: str
    targets: list
    targets_context: str
    phase1_ok: bool
    error: str = ""


# ── Helpers ────────────────────────────────────────────────────────────────────

def _som_type(mr_type: str) -> str:
    return _SOM_TYPE.get(mr_type, "general")


def _read_targets(state_dir: str) -> list:
    p = Path(state_dir) / "targets.json"
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text())
        if isinstance(data, dict):
            return data.get("dimensions", [])
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _format_targets(state_dir: str, targets: list) -> str:
    if not targets:
        return ""
    lines = [
        "╔══════════════════════════════════════════════════╗",
        "║  SoM Pipeline — Active Targets                    ║",
        "╚══════════════════════════════════════════════════╝",
        f"State: {state_dir}",
        "",
        "Success Criteria:",
    ]
    for t in targets:
        name = t.get("name", t.get("target", "?"))
        weight = t.get("weight", 0)
        mtype = t.get("measure_type", "?")
        lines.append(f"  • {name} (weight: {weight}, measure: {mtype})")
    total = sum(t.get("weight", 0) for t in targets)
    lines.append(f"\nTotal weight: {total}/100")
    lines.append(f"Write your complete response to: {state_dir}/output.md")
    lines.append("══════════════════════════════════════════════════")
    return "\n".join(lines)


def _parse_json_from_stdout(stdout: str) -> dict:
    """Extract the last JSON object from subprocess stdout."""
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except Exception:
                pass
    try:
        return json.loads(stdout.strip())
    except Exception:
        return {}


def _find_state_dir(task_text: str) -> Optional[str]:
    """Fallback: find most-recent state dir matching task slug."""
    state_root = _WORKSPACE / "rql/state"
    if not state_root.exists():
        return None
    slug = re.sub(r'[^a-z0-9-]', '-', task_text.lower()[:40]).strip('-')[:30]
    slug = re.sub(r'-+', '-', slug)
    candidates = sorted(
        [d for d in state_root.iterdir()
         if d.is_dir() and d.name.startswith(slug) and (d / "targets.json").exists()],
        key=lambda d: d.stat().st_mtime, reverse=True,
    )
    return str(candidates[0]) if candidates else None


# ── Public API ─────────────────────────────────────────────────────────────────

def run_phase1(task_text: str, mr_type: str) -> PrepResult:
    """
    Generate SoM targets for this task.
    Fast (rule-based, no LLM). Uses --tier trivial to skip context gathering.
    Returns PrepResult with targets context string for injection into user_message.
    """
    import subprocess as _subp

    if not _PIPELINE.exists():
        return PrepResult("", "", [], "", False, "som_pipeline.py not found")

    (_WORKSPACE / "rql/state").mkdir(parents=True, exist_ok=True)
    som_t = _som_type(mr_type)

    try:
        proc = _subp.run(
            [sys.executable, str(_PIPELINE),
             "--task", task_text,
             "--task-type", som_t,
             "--tier", "trivial"],
            capture_output=True, text=True, timeout=30,
        )
    except _subp.TimeoutExpired:
        return PrepResult("", "", [], "", False, "Phase 1 timeout (30s)")
    except Exception as e:
        return PrepResult("", "", [], "", False, str(e))

    data = _parse_json_from_stdout(proc.stdout)
    state_dir = data.get("state_dir", "")

    if not state_dir or not Path(state_dir).exists():
        # Fallback: search state root by task slug
        state_dir = _find_state_dir(task_text) or ""

    if not state_dir:
        return PrepResult("", "", [], "", False,
                          f"State dir not found. Return code: {proc.returncode}. "
                          f"Stderr: {proc.stderr[:200]}")

    task_id = Path(state_dir).name
    targets = _read_targets(state_dir)
    ctx = _format_targets(state_dir, targets) if targets else ""

    return PrepResult(
        state_dir=state_dir, task_id=task_id,
        targets=targets, targets_context=ctx, phase1_ok=True,
    )


def _run_phase2_blocking(
    task_text: str, mr_type: str, state_dir: str, response_text: str
) -> dict:
    """Write output.md and run complete_pipeline. Returns score/oracle dict."""
    import subprocess as _subp

    if not state_dir or not Path(state_dir).exists():
        return {"error": "state dir missing", "score": 0.0, "oracle": "SKIPPED"}

    # Write LLM response as output artifact — but prefer what the agent wrote via tool calls
    out_md = Path(state_dir) / "output.md"
    agent_wrote = out_md.exists() and out_md.stat().st_size >= 50
    if not agent_wrote:
        try:
            out_md.write_text(response_text, encoding="utf-8")
        except Exception as e:
            return {"error": f"write output.md: {e}", "score": 0.0, "oracle": "SKIPPED"}

    task_id = Path(state_dir).name
    som_t = _som_type(mr_type)

    try:
        proc = _subp.run(
            [sys.executable, str(_PIPELINE),
             "--task", task_text,
             "--task-type", som_t,
             "--complete",
             "--task-id", task_id,
             "--state-dir", state_dir],
            capture_output=True, text=True, timeout=120,
        )
    except _subp.TimeoutExpired:
        return {"error": "Phase 2 timeout (120s)", "score": 0.0, "oracle": "TIMEOUT"}
    except Exception as e:
        return {"error": str(e), "score": 0.0, "oracle": "ERROR"}

    data = _parse_json_from_stdout(proc.stdout)
    score = float(data.get("score", 0.0))
    passed = data.get("passed", False)
    raw_oracle = data.get("oracle", {})
    if isinstance(raw_oracle, dict):
        oracle_verdict = raw_oracle.get("verdict", "FAIL")
    else:
        oracle_verdict = str(raw_oracle) if raw_oracle else ("PASS" if passed else "FAIL")
    return {"score": score, "oracle": oracle_verdict, "error": None}


def run_phase2_async(
    request_id: str,
    task_type: str,
    task_text: str,
    state_dir: str,
    response_text: str,
    start_time: float,
) -> None:
    """
    Score the LLM response in a background daemon thread.
    Writes output.md, runs complete_pipeline, logs outcome. Never blocks caller.
    """
    def _worker():
        result = _run_phase2_blocking(task_text, task_type, state_dir, response_text)
        _log_outcome(
            request_id=request_id,
            task_type=task_type,
            score=result.get("score"),
            oracle=result.get("oracle"),
            error=result.get("error"),
            start_time=start_time,
        )

    threading.Thread(target=_worker, daemon=True, name="mr-als-phase2").start()


def run_outcome_only(
    request_id: str,
    task_type: str,
    start_time: float,
    error: Optional[str] = None,
) -> None:
    """Log routing outcome when no SoM pipeline ran (bypassed or Phase 1 failed)."""
    _log_outcome(
        request_id=request_id,
        task_type=task_type,
        score=None,
        oracle="SKIPPED",
        error=error,
        start_time=start_time,
    )


_LOG_WRITER_PATH = _EXP_DIR / "log_writer.py"


def _load_log_writer():
    """Load log_writer module via importlib (hyphenated dir → can't use package import)."""
    import importlib.util as _ilu
    if not _LOG_WRITER_PATH.exists():
        return None
    try:
        spec = _ilu.spec_from_file_location("_mr_log_writer_ex", _LOG_WRITER_PATH)
        mod = _ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


def _log_outcome(
    request_id: str,
    task_type: str,
    score: Optional[float],
    oracle: Optional[str],
    error: Optional[str],
    start_time: float,
) -> None:
    """Append one record to routing_outcomes.jsonl. Never raises."""
    try:
        mod = _load_log_writer()
        if mod is None:
            return
        log_routing_outcome = mod.log_routing_outcome
        log_routing_outcome(
            request_id=request_id,
            task_type=task_type,
            composite_score=score,
            som_score=score,
            oracle_verdict=oracle or "SKIPPED",
            adv_pass_clean=None,
            latency_ms=round((time.time() - start_time) * 1000),
            error=error,
        )
    except Exception:
        pass
