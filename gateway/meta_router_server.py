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


class ClassifyResponse(BaseModel):
    type: str
    mode: str
    confidence: float
    directive: str
    text_with_directive: str


@app.post("/classify", response_model=ClassifyResponse)
def classify_message(req: ClassifyRequest) -> ClassifyResponse:
    if not req.text:
        raise HTTPException(status_code=400, detail="text must not be empty")

    decision = make_route_decision(req.text, source="api", surface="http")

    return ClassifyResponse(
        type=decision.type,
        mode=decision.mode,
        confidence=decision.confidence,
        directive=decision.directive,
        text_with_directive=prepend_directive(req.text, decision),
    )


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": "2.0.0"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="127.0.0.1", port=3120, reload=False)
