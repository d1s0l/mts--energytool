"""Создание сессий и чтение истории из PostgreSQL."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request

from app.schemas import SessionDetailOut, SessionListOut, SessionOut

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@router.post("", status_code=201, response_model=SessionOut)
async def create_session(request: Request):
    async with request.app.state.db_pool.connection() as conn:
        cursor = await conn.execute("INSERT INTO sessions DEFAULT VALUES RETURNING *")
        return await cursor.fetchone()


@router.get("", response_model=SessionListOut)
async def list_sessions(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    async with request.app.state.db_pool.connection() as conn:
        # Список и total должны отражать один снимок данных.
        await conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        cursor = await conn.execute("SELECT count(*) AS total FROM sessions")
        total = (await cursor.fetchone())["total"]
        cursor = await conn.execute(
            "SELECT id, state, created_at FROM sessions "
            "ORDER BY created_at DESC, id LIMIT %s OFFSET %s",
            (limit, offset),
        )
        return {"items": await cursor.fetchall(), "total": total, "limit": limit, "offset": offset}


@router.get("/{session_id}", response_model=SessionDetailOut)
async def get_session(session_id: UUID, request: Request):
    async with request.app.state.db_pool.connection() as conn:
        await conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        cursor = await conn.execute(
            "SELECT id, state, created_at FROM sessions WHERE id = %s", (session_id,)
        )
        session = await cursor.fetchone()
        if session is None:
            raise HTTPException(status_code=404, detail="session_not_found")
        cursor = await conn.execute(
            "SELECT id, role, text, created_at, debug FROM messages "
            "WHERE session_id = %s ORDER BY position",
            (session_id,),
        )
        return {**session, "messages": await cursor.fetchall()}
