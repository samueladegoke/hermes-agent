"""Best-effort LLM fallback for ambiguous meta-router classifications."""
from __future__ import annotations

import json
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gateway.meta_router import RouteResult


_LLM_MODEL = "gpt-5.4-mini"
_LLM_TIMEOUT_SECONDS = 8.0
_VALID_TYPES = ("code", "audit", "research", "production", "integration", "config", "design")
_VALID_MODES = ("execute", "plan", "review", "urgent")


def _responses_text_input(prompt: str) -> list[dict]:
    return [{"role": "user", "content": [{"type": "input_text", "text": str(prompt or "")}] }]



def _run_with_timeout(fn, timeout_seconds: float):
    result: dict[str, object] = {}
    error: dict[str, BaseException] = {}

    def _target():
        try:
            result["value"] = fn()
        except BaseException as exc:  # pragma: no cover - defensive capture
            error["exc"] = exc

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join(timeout_seconds)
    if thread.is_alive():
        return None
    if "exc" in error:
        raise error["exc"]
    return result.get("value")



def _build_system_prompt() -> str:
    return (
        "You are a task router for an AI coding agent.\n"
        "Choose exactly one type and one mode. Respond with valid JSON only.\n\n"
        "Type definitions:\n"
        "  code        — write, fix, refactor, or implement code; scripts; functions; tests\n"
        "  audit       — security review, vulnerability scan, compliance, pentest\n"
        "  research    — list, inventory, explain, summarise, investigate, look up, compare;\n"
        "               also use for discovery tasks like 'what is active', 'list all X'\n"
        "  production  — deploy, release, rollback, incident response, monitoring, on-call\n"
        "  integration — API, webhook, OAuth, SDK, middleware, MCP plugin connections\n"
        "  config      — setup, configuration files, env vars, systemd, nginx, TLS\n"
        "  design      — UI/UX, frontend layouts, CSS, mockups, Figma, visual components\n"
        "               (NOT system architecture, database schema, or general planning)\n\n"
        "Mode definitions:\n"
        "  execute — do the task directly\n"
        "  plan    — outline steps or strategy, do not execute yet\n"
        "  review  — check or verify existing work\n"
        "  urgent  — time-critical, treat as highest priority"
    )


def _build_user_prompt(task: str, keyword_result) -> str:
    hint = (
        f"Keyword classifier suggested type={keyword_result.type}, "
        f"confidence={keyword_result.confidence:.2f}. "
        "Confidence is low — use your own judgement based on the type definitions."
    )
    return (
        f"Task:\n{task}\n\n"
        f"Hint: {hint}\n\n"
        "Reply with JSON only:\n"
        '{"type": "...", "mode": "...", "confidence": 0.0, "reasoning": "one sentence"}'
    )


def _extract_output_text(response) -> str:
    text = getattr(response, "output_text", None)
    if isinstance(text, str) and text.strip():
        return text.strip()

    output = getattr(response, "output", None)
    if isinstance(output, list):
        chunks: list[str] = []
        for item in output:
            content = getattr(item, "content", None)
            if not isinstance(content, list):
                continue
            for part in content:
                part_text = getattr(part, "text", None)
                if isinstance(part_text, str) and part_text.strip():
                    chunks.append(part_text.strip())
        if chunks:
            return "\n".join(chunks)

    if isinstance(response, dict):
        text = response.get("output_text")
        if isinstance(text, str) and text.strip():
            return text.strip()

    return ""


def _stream_text_response(client, *, instructions: str, prompt: str) -> str:
    def _run() -> str:
        deltas: list[str] = []
        with client.responses.stream(
            model=_LLM_MODEL,
            instructions=instructions,
            input=_responses_text_input(prompt),
            reasoning={"effort": "xhigh", "summary": "auto"},
            service_tier="priority",
            text={"verbosity": "low"},
            store=False,
        ) as stream:
            for event in stream:
                event_type = getattr(event, "type", "")
                if event_type == "response.output_text.delta":
                    delta = getattr(event, "delta", None)
                    if isinstance(delta, str) and delta:
                        deltas.append(delta)
            response = stream.get_final_response()
        text = "".join(deltas).strip()
        if text:
            return text
        return _extract_output_text(response)

    streamed = _run_with_timeout(_run, _LLM_TIMEOUT_SECONDS)
    if not isinstance(streamed, str):
        return ""
    return streamed


def _parse_json_payload(text: str) -> dict:
    payload = (text or "").strip()
    if not payload:
        raise ValueError("empty JSON payload")
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        start = payload.find("{")
        end = payload.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        return json.loads(payload[start : end + 1])


def _coerce_confidence(value) -> float | None:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return None
    if confidence < 0.0:
        return 0.0
    if confidence > 1.0:
        return 1.0
    return confidence


def _build_client(timeout_seconds: float):
    from openai import OpenAI

    from agent.auxiliary_client import _to_openai_base_url
    from hermes_cli.runtime_provider import resolve_runtime_provider

    runtime = resolve_runtime_provider(requested="openai-codex")
    api_key = str(runtime.get("api_key") or "").strip()
    if not api_key:
        return None
    base_url = _to_openai_base_url(str(runtime.get("base_url") or "").strip())
    return OpenAI(api_key=api_key, base_url=base_url, timeout=timeout_seconds)


def llm_classify(task: str, keyword_result) -> "RouteResult | None":
    """Return an LLM routing decision for ambiguous tasks, or None on failure."""
    if not task or not str(task).strip():
        return None

    try:
        client = _build_client(_LLM_TIMEOUT_SECONDS)
        if client is None:
            return None

        payload = _parse_json_payload(
            _stream_text_response(
                client,
                instructions=_build_system_prompt(),
                prompt=_build_user_prompt(task, keyword_result),
            )
        )
    except Exception:
        return None

    result_type = str(payload.get("type") or "").strip().lower()
    result_mode = str(payload.get("mode") or "").strip().lower()
    confidence = _coerce_confidence(payload.get("confidence"))
    if result_type not in _VALID_TYPES or result_mode not in _VALID_MODES or confidence is None:
        return None

    from gateway.meta_router import RouteResult

    directive = f"[META-ROUTER | {result_type} | {result_mode}]"
    return RouteResult(
        type=result_type,
        mode=result_mode,
        confidence=confidence,
        directive=directive,
    )
