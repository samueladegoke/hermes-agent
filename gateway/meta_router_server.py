"""
Meta-Router API Server v2.0
FastAPI wrapper around route.py — port 3120.

Start:
    uvicorn server:app --host 127.0.0.1 --port 3120

Or via Task Scheduler / Windows service.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from route import classify, prepend_directive

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
