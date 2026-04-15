#!/usr/bin/env python3
"""
meta_router_executor.py — MR-ALS Phase 1+2: SoM Execution Adapter

Phase 1 (pre-LLM):  run som_pipeline --tier trivial to generate targets
Phase 2 (post-LLM): run complete_pipeline + ADV_PASS + log outcome

Phase 4+ threshold trigger: every 10 new outcomes → launch mr_als_runner.py
  --phase 4,5 --force in a background subprocess to regenerate + re-evaluate
  candidate artifacts.

Used by run_agent.py (imported at module level).

NOTE: meta-router/ directory has a hyphen — cannot be a Python package.
      log_writer.py is loaded via importlib.util.spec_from_file_location.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# ── Absolute paths ─────────────────────────────────────────────────────────────
_MR_DIR = Path("/home/samade10/.openclaw/workspace/skills/maintainer/meta-router")
_EXP_DIR = _MR_DIR / "experience"
_SCRIPTS_DIR = _MR_DIR / "scripts"
_LOG_WRITER_PATH = _EXP_DIR / "log_writer.py"
_OUTCOMES_JSONL = _EXP_DIR / "routing_outcomes.jsonl"
_RUNNER_PATH = _SCRIPTS_DIR / "mr_als_runner.py"

_SOM_DIR = Path("/home/samade10/.openclaw/workspace/skills/process/som")
_SOM_PIPELINE = _SOM_DIR / "som_pipeline.py"

# ── Log writer lazy-init ───────────────────────────────────────────────────────
_lw_mod = None
_lw_lock = threading.Lock()


def _load_log_writer():
    global _lw_mod
    if _lw_mod is not None:
        return _lw_mod
    with _lw_lock:
        if _lw_mod is not None:
            return _lw_mod
        if not _LOG_WRITER_PATH.exists():
            return None
        try:
            spec = importlib.util.spec_from_file_location("_mr_log_writer", _LOG_WRITER_PATH)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _lw_mod = mod
        except Exception:
            pass
    return _lw_mod


# ── MR type → SoM type map ─────────────────────────────────────────────────────
_MR_TO_SOM_TYPE = {
    "code":        "code",
    "audit":       "general",
    "research":    "research",
    "production":  "general",
    "integration": "code",
    "design":      "design",
    "config":      "config",
}


# ── PrepResult ─────────────────────────────────────────────────────────────────

@dataclass
class PrepResult:
    som_state_dir: Optional[Path]
    targets_context: str  # formatted block for injection into task text
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.som_state_dir is not None and not self.error


# ── Phase 1: pre-LLM SoM target generation ────────────────────────────────────

def run_phase1(task_text: str, mr_type: str) -> PrepResult:
    """
    Run SoM --tier trivial to generate routing targets before the LLM call.
    Returns PrepResult with state_dir and formatted targets_context.
    Never raises — returns PrepResult with error= on failure.
    """
    if not _SOM_PIPELINE.exists():
        return PrepResult(None, "", error="som_pipeline.py not found")

    som_type = _MR_TO_SOM_TYPE.get(mr_type, "code")
    try:
        result = subprocess.run(
            [
                sys.executable, str(_SOM_PIPELINE),
                "--tier", "trivial",
                "--task-type", som_type,
                "--task-text", task_text[:2000],
                "--output-format", "json",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return PrepResult(None, "", error=f"som_pipeline exit {result.returncode}: {result.stderr[:200]}")

        # Parse JSON from stdout
        stdout = result.stdout.strip()
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            # Try to find JSON block in mixed output
            for line in reversed(stdout.splitlines()):
                line = line.strip()
                if line.startswith("{"):
                    try:
                        data = json.loads(line)
                        break
                    except json.JSONDecodeError:
                        pass
            else:
                return PrepResult(None, "", error="Could not parse som_pipeline JSON output")

        state_dir = data.get("state_dir") or data.get("output_dir")
        if not state_dir:
            return PrepResult(None, "", error="som_pipeline returned no state_dir")

        state_path = Path(state_dir)
        targets_file = state_path / "targets.json"
        if not targets_file.exists():
            return PrepResult(state_path, "", error="targets.json not found in state_dir")

        targets = json.loads(targets_file.read_text())
        targets_context = _format_targets(targets, mr_type)
        return PrepResult(state_path, targets_context)

    except subprocess.TimeoutExpired:
        return PrepResult(None, "", error="som_pipeline timed out (>30s)")
    except Exception as e:
        return PrepResult(None, "", error=f"phase1 exception: {e}")


def _format_targets(targets: dict | list, mr_type: str) -> str:
    """Format targets dict/list into an injectable context block."""
    try:
        if isinstance(targets, dict):
            items = targets.get("targets", targets.get("items", []))
        else:
            items = targets

        if not items:
            return ""

        lines = [f"\n[SoM Targets | {mr_type}]"]
        for i, item in enumerate(items[:8], 1):
            if isinstance(item, dict):
                label = item.get("label") or item.get("name") or item.get("target", "")
                desc = item.get("description") or item.get("desc", "")
                lines.append(f"  {i}. {label}" + (f" — {desc}" if desc else ""))
            else:
                lines.append(f"  {i}. {item}")
        lines.append("[/SoM Targets]\n")
        return "\n".join(lines)
    except Exception:
        return ""


# ── Phase 2: post-LLM outcome logging ─────────────────────────────────────────

def _count_outcomes() -> int:
    if not _OUTCOMES_JSONL.exists():
        return 0
    try:
        return sum(1 for line in _OUTCOMES_JSONL.read_text().splitlines() if line.strip())
    except Exception:
        return 0


def _trigger_optimizer_bg() -> None:
    """
    Launch mr_als_runner.py --phase 4,5 --force in a background subprocess.
    Daemon thread — does not block caller. Silent on any error.
    """
    if not _RUNNER_PATH.exists():
        return

    def _run():
        try:
            subprocess.run(
                [sys.executable, str(_RUNNER_PATH), "--phase", "4,5", "--force"],
                capture_output=True,
                timeout=300,
            )
        except Exception:
            pass

    t = threading.Thread(target=_run, daemon=True, name="mr-als-optimizer")
    t.start()


def run_phase2_async(
    request_id: str,
    task_type: str,
    task_text: str,
    som_state_dir: Optional[Path],
    final_response: str,
    t0: float,
    routing_artifact_version: str = "static-default",
    session_id: Optional[str] = None,
) -> None:
    """
    Spawn a background daemon thread that:
      1. Writes final_response to output.md (if agent didn't write it already)
      2. Runs the complete SoM pipeline (ADV_PASS + evidence_contract)
      3. Logs the routing outcome (outcome_quality, latency_ms)
      4. Checks threshold → triggers optimizer if needed

    Never blocks the caller. Never raises.
    """
    def _worker():
        _do_phase2(request_id, task_type, task_text, som_state_dir,
                   final_response, t0, routing_artifact_version, session_id)

    t = threading.Thread(target=_worker, daemon=True, name=f"mr-p2-{request_id[:8]}")
    t.start()


def _do_phase2(
    request_id: str,
    task_type: str,
    task_text: str,
    som_state_dir: Optional[Path],
    final_response: str,
    t0: float,
    routing_artifact_version: str,
    session_id: Optional[str],
) -> None:
    """Inner blocking implementation of Phase 2. Runs in background thread."""
    latency_ms = round((time.time() - t0) * 1000, 1)
    outcome_quality = 50.0  # default if pipeline can't run

    try:
        if som_state_dir and _SOM_PIPELINE.exists():
            out_md = som_state_dir / "output.md"

            # Only write final_response if agent didn't already produce output.md
            agent_wrote = out_md.exists() and out_md.stat().st_size >= 50
            if not agent_wrote and final_response and final_response.strip():
                out_md.write_text(final_response)

            # Run complete pipeline (ADV_PASS + evidence_contract)
            som_type = _MR_TO_SOM_TYPE.get(task_type, "code")
            result = subprocess.run(
                [
                    sys.executable, str(_SOM_PIPELINE),
                    "--complete",
                    "--task-type", som_type,
                    "--state-dir", str(som_state_dir),
                    "--output", str(out_md),
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )

            # Parse SoM score from output
            if result.returncode == 0:
                stdout = result.stdout
                for line in stdout.splitlines():
                    line = line.strip()
                    # Look for JSON with score key
                    if line.startswith("{") and "score" in line:
                        try:
                            d = json.loads(line)
                            score = d.get("score") or d.get("total_score") or d.get("quality_score")
                            if score is not None:
                                outcome_quality = float(score)
                                break
                        except Exception:
                            pass
                    # Look for "Score: N" pattern
                    import re
                    m = re.search(r"(?:score|quality)[:\s]+([0-9]+\.?[0-9]*)", line, re.IGNORECASE)
                    if m:
                        outcome_quality = float(m.group(1))
                        break

    except Exception:
        pass  # never raise from background thread

    # Log outcome
    lw = _load_log_writer()
    if lw and hasattr(lw, "log_routing_outcome"):
        try:
            lw.log_routing_outcome(
                request_id=request_id,
                task_type=task_type,
                outcome_quality=outcome_quality,
                latency_ms=latency_ms,
                routing_artifact_version=routing_artifact_version,
                session_id=session_id,
            )
        except Exception:
            pass

    # Threshold trigger: every 10 outcomes → optimizer
    try:
        n = _count_outcomes()
        if n >= 10 and n % 10 == 0:
            _trigger_optimizer_bg()
    except Exception:
        pass


# ── Convenience: log outcome only (no SoM pipeline) ───────────────────────────

def log_outcome_only(
    request_id: str,
    task_type: str,
    t0: float,
    routing_artifact_version: str = "static-default",
    session_id: Optional[str] = None,
) -> None:
    """
    Log a routing outcome without running the SoM pipeline.
    Used when Phase 1 prep was skipped (short task, low confidence).
    """
    latency_ms = round((time.time() - t0) * 1000, 1)
    lw = _load_log_writer()
    if lw and hasattr(lw, "log_routing_outcome"):
        try:
            lw.log_routing_outcome(
                request_id=request_id,
                task_type=task_type,
                outcome_quality=50.0,  # unknown — no SoM score
                latency_ms=latency_ms,
                routing_artifact_version=routing_artifact_version,
                session_id=session_id,
            )
        except Exception:
            pass
