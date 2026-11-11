"""Готовность приложения без сетевых вызовов к моделям."""

import asyncio
from typing import Literal

from fastapi import APIRouter, Request, Response
from psycopg import InterfaceError, OperationalError
from psycopg_pool import PoolClosed, PoolTimeout
from pydantic import BaseModel

CheckStatus = Literal["ready", "not_ready", "not_configured"]


class HealthChecks(BaseModel):
    database: CheckStatus
    rag: CheckStatus
    consultant: CheckStatus
    lead_summarizer: CheckStatus


class HealthOut(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: HealthChecks


router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health", response_model=HealthOut, responses={503: {"model": HealthOut}})
async def health(request: Request, response: Response) -> HealthOut:
    state = request.app.state
    pool = getattr(state, "db_pool", None)
    database: CheckStatus = "not_configured"
    if pool is not None:
        try:
            async with asyncio.timeout(2):
                async with pool.connection() as conn:
                    await conn.execute("SELECT 1")
            database = "ready"
        except (TimeoutError, OperationalError, InterfaceError, PoolClosed, PoolTimeout):
            database = "not_ready"

    search = getattr(state, "search", None)
    checks = HealthChecks(
        database=database,
        rag="not_configured"
        if search is None
        else ("ready" if search.engine.chunks else "not_ready"),
        consultant="ready" if callable(getattr(state, "consultant", None)) else "not_configured",
        lead_summarizer=(
            "ready" if callable(getattr(state, "lead_summarizer", None)) else "not_configured"
        ),
    )
    ready = all(value == "ready" for value in checks.model_dump().values())
    response.status_code = 200 if ready else 503
    return HealthOut(status="ready" if ready else "not_ready", checks=checks)
