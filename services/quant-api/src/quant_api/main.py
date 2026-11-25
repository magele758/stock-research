"""FastAPI app (`uvicorn quant_api.main:app --port 8100`)."""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import deps
from .jobs import get_manager, get_store
from .routes import router

log = logging.getLogger("quant_api")

_EXTRA_CORS = [
    origin.strip()
    for origin in os.environ.get("QUANT_CORS_ORIGINS", "").split(",")
    if origin.strip()
]
CORS_ORIGINS = list(dict.fromkeys(["http://127.0.0.1:3100", "http://localhost:3100", *_EXTRA_CORS]))


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    try:
        snap = deps.require_snapshot(None)
        log.info("quant-api ready on snapshot %s", snap.snapshot_id)
    except deps.ServiceUnavailable as exc:
        # Never synthesise market data: start degraded and tell the operator what to run.
        log.warning("quant-api starting WITHOUT data: %s", exc.message)
    try:
        get_store().fail_orphans()
    except Exception as exc:  # noqa: BLE001
        log.warning("job store init failed: %s", exc)
    yield
    get_manager().shutdown()


app = FastAPI(
    title="Pro-Quant V2.0 API",
    version="0.2.0",
    description="Deterministic quant research API. Read-only: no broker or order routes.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)
app.include_router(router)


def _err(status: int, code: str, message: str, retryable: bool = False, **extra: object) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "retryable": retryable, **extra}},
    )


@app.exception_handler(deps.ServiceUnavailable)
async def _unavailable(_: Request, exc: deps.ServiceUnavailable) -> JSONResponse:
    return _err(503, exc.code, exc.message, exc.retryable, hint="run `make seed`" if "make seed" in exc.message else None)


@app.exception_handler(deps.NotFound)
async def _not_found(_: Request, exc: deps.NotFound) -> JSONResponse:
    return _err(404, "NOT_FOUND", exc.message)


@app.exception_handler(deps.InvalidArgument)
async def _invalid(_: Request, exc: deps.InvalidArgument) -> JSONResponse:
    return _err(422, "INVALID_ARGUMENT", exc.message)


@app.exception_handler(ValueError)
async def _value_error(_: Request, exc: ValueError) -> JSONResponse:
    return _err(422, "INVALID_ARGUMENT", str(exc))
