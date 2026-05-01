"""
Meta-Router API Server v2.0
FastAPI wrapper around the shared meta-router runtime on port 3120.
"""
from __future__ import annotations

import hmac
import os
import time
from collections import defaultdict, deque
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel
from starlette.responses import JSONResponse

from gateway.meta_router_executor import run_outcome_only
from gateway.meta_router_runtime import make_route_decision

app = FastAPI(title="Meta-Router", version="2.0.0")

MAX_CLASSIFY_BODY_BYTES = 8 * 1024
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_REQUESTS = 240
_OUTCOME_TOKEN_PATH_ENV = "META_ROUTER_OUTCOME_TOKEN_PATH"
_OUTCOME_TOKEN_ENV = "META_ROUTER_OUTCOME_TOKEN"
_DEFAULT_OUTCOME_TOKEN_PATH = Path("~/.config/meta-router/outcome.token")
_STARTED_AT = time.time()

_METRICS: dict[str, int] = {
    "classify_total": 0,
    "classify_bypassed_total": 0,
    "classify_errors_total": 0,
    "classify_rate_limited_total": 0,
    "classify_oversize_total": 0,
    "outcome_total": 0,
    "outcome_errors_total": 0,
    "outcome_unauthorized_total": 0,
    "malformed_total": 0,
}
_RATE_LIMIT_BUCKETS: dict[str, deque[float]] = defaultdict(deque)


def _inc(metric: str, amount: int = 1) -> None:
    _METRICS[metric] = _METRICS.get(metric, 0) + amount


def _client_key(request: Request) -> str:
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _outcome_token_path() -> Path:
    configured = os.environ.get(_OUTCOME_TOKEN_PATH_ENV)
    return Path(configured).expanduser() if configured else _DEFAULT_OUTCOME_TOKEN_PATH.expanduser()


def _load_outcome_token() -> str:
    from_env = os.environ.get(_OUTCOME_TOKEN_ENV, "").strip()
    if from_env:
        return from_env
    path = _outcome_token_path()
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""
    except OSError:
        return ""


def _require_outcome_bearer(authorization: str | None) -> None:
    """Require a configured bearer token before accepting outcome writes.

    The token comes from META_ROUTER_OUTCOME_TOKEN or, preferably for services,
    ~/.config/meta-router/outcome.token.  If neither is configured, reject writes
    closed instead of silently allowing unauthenticated mutation.
    """
    expected = _load_outcome_token()
    if not expected:
        _inc("outcome_unauthorized_total")
        raise HTTPException(status_code=503, detail="outcome auth token is not configured")
    prefix = "Bearer "
    if not authorization or not authorization.startswith(prefix):
        _inc("outcome_unauthorized_total")
        raise HTTPException(status_code=401, detail="missing bearer token")
    provided = authorization[len(prefix):].strip()
    if not hmac.compare_digest(provided, expected):
        _inc("outcome_unauthorized_total")
        raise HTTPException(status_code=401, detail="invalid bearer token")


@app.middleware("http")
async def _classify_safety_limits(request: Request, call_next):
    if request.method.upper() == "POST" and request.url.path == "/classify":
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > MAX_CLASSIFY_BODY_BYTES:
                    _inc("classify_oversize_total")
                    _inc("malformed_total")
                    return JSONResponse({"detail": "request body too large"}, status_code=413)
            except ValueError:
                _inc("malformed_total")
                return JSONResponse({"detail": "invalid content-length"}, status_code=400)

        now = time.time()
        bucket = _RATE_LIMIT_BUCKETS[_client_key(request)]
        while bucket and now - bucket[0] > RATE_LIMIT_WINDOW_SECONDS:
            bucket.popleft()
        if len(bucket) >= RATE_LIMIT_MAX_REQUESTS:
            _inc("classify_rate_limited_total")
            return JSONResponse({"detail": "rate limit exceeded"}, status_code=429)
        bucket.append(now)

    return await call_next(request)


class ClassifyRequest(BaseModel):
    text: str
    source: str = "api"
    surface: str = "http"
    session_id: str | None = None


class ClassifyResponse(BaseModel):
    request_id: str
    type: str
    mode: str
    confidence: float
    directive: str
    prepend_text: str
    text_with_directive: str
    primary: str
    secondary: str | None = None
    budget_multiplier: float
    routing_artifact_version: str
    bypassed: bool
    bypass_reason: str = ""
    memory_need: str
    memory_authority: list[str]
    required_tools: list[str]
    optional_tools: list[str]
    skip_tools: list[str]
    max_memory_steps: int
    memory_policy_version: str


class OutcomeRequest(BaseModel):
    request_id: str
    task_type: str
    session_id: str | None = None
    source: str = "api"
    surface: str = "http"
    routing_artifact_version: str = "static-default"
    success: bool = True
    error: str | None = None
    duration_ms: float | None = None
    notes: list[str] | None = None


class OutcomeResponse(BaseModel):
    status: str


@app.post("/classify", response_model=ClassifyResponse)
def classify_message(req: ClassifyRequest) -> ClassifyResponse:
    if not req.text:
        _inc("malformed_total")
        raise HTTPException(status_code=400, detail="text must not be empty")

    try:
        decision = make_route_decision(
            req.text,
            source=req.source or "api",
            surface=req.surface or "http",
            session_id=req.session_id,
        )
    except Exception:
        _inc("classify_errors_total")
        raise

    _inc("classify_total")
    if decision.bypassed:
        _inc("classify_bypassed_total")
    prepend_text = getattr(decision, "prepend_text", decision.directive) if not decision.bypassed else ""
    text_with_directive = req.text if decision.bypassed or not prepend_text else f"{prepend_text}\n\n{req.text}"

    return ClassifyResponse(
        request_id=decision.request_id,
        type=decision.type,
        mode=decision.mode,
        confidence=decision.confidence,
        directive=decision.directive,
        prepend_text=prepend_text,
        text_with_directive=text_with_directive,
        primary=decision.primary,
        secondary=decision.secondary,
        budget_multiplier=decision.budget_multiplier,
        routing_artifact_version=decision.routing_artifact_version,
        bypassed=decision.bypassed,
        bypass_reason=decision.bypass_reason,
        memory_need=decision.memory_need,
        memory_authority=list(decision.memory_authority or []),
        required_tools=list(decision.required_tools or []),
        optional_tools=list(decision.optional_tools or []),
        skip_tools=list(decision.skip_tools or []),
        max_memory_steps=decision.max_memory_steps,
        memory_policy_version=decision.memory_policy_version,
    )


@app.post("/outcome", response_model=OutcomeResponse)
def log_terminal_outcome(req: OutcomeRequest, authorization: str | None = Header(default=None)) -> OutcomeResponse:
    _require_outcome_bearer(authorization)
    if not req.request_id.strip():
        _inc("malformed_total")
        raise HTTPException(status_code=400, detail="request_id must not be empty")
    if not req.task_type.strip():
        _inc("malformed_total")
        raise HTTPException(status_code=400, detail="task_type must not be empty")

    duration_ms = max(float(req.duration_ms or 0.0), 0.0)
    t0 = time.time() - (duration_ms / 1000.0)
    notes_extra = list(req.notes or [])
    notes_extra.append(f"success={str(bool(req.success)).lower()}")

    try:
        run_outcome_only(
            request_id=req.request_id,
            task_type=req.task_type,
            t0=t0,
            routing_artifact_version=req.routing_artifact_version,
            session_id=req.session_id,
            source=req.source or "api",
            surface=req.surface or "http",
            error=req.error or None,
            notes_extra=notes_extra,
        )
    except Exception:
        _inc("outcome_errors_total")
        raise

    _inc("outcome_total")
    return OutcomeResponse(status="ok")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": "2.0.0"}


@app.get("/metrics")
def metrics() -> dict:
    return {
        "status": "ok",
        "uptime_seconds": round(time.time() - _STARTED_AT, 3),
        "limits": {
            "classify_body_bytes": MAX_CLASSIFY_BODY_BYTES,
            "classify_rate_limit_per_minute": RATE_LIMIT_MAX_REQUESTS,
        },
        "counters": dict(_METRICS),
        "rate_limit_buckets": len(_RATE_LIMIT_BUCKETS),
        "outcome_auth_configured": bool(_load_outcome_token()),
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("gateway.meta_router_server:app", host="127.0.0.1", port=3120, reload=False)
