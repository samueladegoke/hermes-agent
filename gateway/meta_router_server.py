"""
Meta-Router API Server v2.0
FastAPI wrapper around route.py — port 3120.

Start:
    uvicorn server:app --host 127.0.0.1 --port 3120

Or via Task Scheduler / Windows service.
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from gateway.meta_router import classify, prepend_directive

# MR-ALS Phase 1: event logging (source=api)
# NOTE: log_writer lives in a hyphenated directory — load via importlib, not package import.
_ALS_EXP = Path("/home/samade10/.openclaw/workspace/skills/maintainer/meta-router/experience")
_ALS_LOGGING = False
_log_event = None
try:
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location("_mr_log_writer", _ALS_EXP / "log_writer.py")
    _lw_mod = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_lw_mod)
    _log_event = _lw_mod.log_routing_event
    _ALS_LOGGING = True
except Exception:
    pass

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

    result = classify(req.text)

    # MR-ALS Phase 1: log routing event (non-blocking, never raises)
    if _ALS_LOGGING:
        try:
            _log_event(
                source="api",
                surface="http",
                task_text=req.text,
                task_type=result.type,
                mode=result.mode,
                confidence=result.confidence,
                bypassed=False,
            )
        except Exception:
            pass

    return ClassifyResponse(
        type=result.type,
        mode=result.mode,
        confidence=result.confidence,
        directive=result.directive,
        text_with_directive=prepend_directive(req.text, result),
    )


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": "2.0.0"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="127.0.0.1", port=3120, reload=False)
