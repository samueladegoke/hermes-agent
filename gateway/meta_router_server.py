"""
Meta-Router API Server v2.0
FastAPI wrapper around the shared meta-router runtime on port 3120.
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from gateway.meta_router import prepend_directive
from gateway.meta_router_runtime import make_route_decision

app = FastAPI(title="Meta-Router", version="2.0.0")


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
    text_with_directive: str
    primary: str
    secondary: str | None = None
    budget_multiplier: float
    routing_artifact_version: str
    bypassed: bool
    bypass_reason: str = ""


@app.post("/classify", response_model=ClassifyResponse)
def classify_message(req: ClassifyRequest) -> ClassifyResponse:
    if not req.text:
        raise HTTPException(status_code=400, detail="text must not be empty")

    decision = make_route_decision(
        req.text,
        source=req.source or "api",
        surface=req.surface or "http",
        session_id=req.session_id,
    )
    text_with_directive = req.text if decision.bypassed or not decision.directive else prepend_directive(req.text, decision)

    return ClassifyResponse(
        request_id=decision.request_id,
        type=decision.type,
        mode=decision.mode,
        confidence=decision.confidence,
        directive=decision.directive,
        text_with_directive=text_with_directive,
        primary=decision.primary,
        secondary=decision.secondary,
        budget_multiplier=decision.budget_multiplier,
        routing_artifact_version=decision.routing_artifact_version,
        bypassed=decision.bypassed,
        bypass_reason=decision.bypass_reason,
    )


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": "2.0.0"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("gateway.meta_router_server:app", host="127.0.0.1", port=3120, reload=False)
