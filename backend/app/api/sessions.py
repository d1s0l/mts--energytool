"""Сессии, история и отправка сообщений."""

import asyncio
from time import perf_counter
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response
from psycopg.errors import LockNotAvailable
from psycopg.types.json import Jsonb

from app.consultant import ConsultantUnavailable
from app.schemas import (
    SendMessageIn,
    SendMessageOut,
    SessionDetailOut,
    SessionListOut,
    SessionOut,
)

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
    include_deleted: bool = False,
):
    async with request.app.state.db_pool.connection() as conn:
        # Список и total должны отражать один снимок данных.
        await conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        cursor = await conn.execute(
            "SELECT count(*) AS total FROM sessions WHERE (%s OR deleted_at IS NULL)",
            (include_deleted,),
        )
        total = (await cursor.fetchone())["total"]
        cursor = await conn.execute(
            "SELECT id, state, created_at, deleted_at FROM sessions "
            "WHERE (%s OR deleted_at IS NULL) "
            "ORDER BY created_at DESC, id LIMIT %s OFFSET %s",
            (include_deleted, limit, offset),
        )
        return {"items": await cursor.fetchall(), "total": total, "limit": limit, "offset": offset}


@router.get("/{session_id}", response_model=SessionDetailOut)
async def get_session(session_id: UUID, request: Request, include_deleted: bool = False):
    async with request.app.state.db_pool.connection() as conn:
        await conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        cursor = await conn.execute(
            "SELECT id, state, created_at, deleted_at FROM sessions "
            "WHERE id = %s AND (%s OR deleted_at IS NULL)",
            (session_id, include_deleted),
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


@router.post("/{session_id}/messages", response_model=SendMessageOut)
async def send_message(session_id: UUID, body: SendMessageIn, request: Request):
    started = perf_counter()
    async with request.app.state.db_pool.connection() as conn:
        # Блокировка действует между процессами и освобождается при commit или rollback.
        try:
            cursor = await conn.execute(
                "SELECT state FROM sessions WHERE id = %s AND deleted_at IS NULL FOR UPDATE NOWAIT",
                (session_id,),
            )
        except LockNotAvailable as exc:
            raise HTTPException(status_code=409, detail="session_busy") from exc
        session = await cursor.fetchone()
        if session is None:
            raise HTTPException(status_code=404, detail="session_not_found")

        consultant = request.app.state.consultant
        if consultant is None:
            raise HTTPException(status_code=503, detail="consultant_unavailable")
        cursor = await conn.execute(
            "SELECT role, text AS content FROM messages WHERE session_id = %s ORDER BY position",
            (session_id,),
        )
        history = await cursor.fetchall()
        try:
            async with asyncio.timeout(60):
                reply = await consultant(
                    text=body.text, history=history, search=request.app.state.search
                )
        except TimeoutError as exc:
            raise HTTPException(status_code=504, detail="consultant_timeout") from exc
        except ConsultantUnavailable as exc:
            raise HTTPException(status_code=503, detail="consultant_unavailable") from exc

        state = "lead_created" if session["state"] == "lead_created" else reply.state
        await conn.execute("UPDATE sessions SET state = %s WHERE id = %s", (state, session_id))
        insert_message = (
            "INSERT INTO messages (session_id, role, text, debug, created_at) "
            "VALUES (%s, %s, %s, %s, clock_timestamp()) RETURNING id, role, text, created_at, debug"
        )
        cursor = await conn.execute(insert_message, (session_id, "user", body.text, None))
        user_message = await cursor.fetchone()
        debug = {
            "total_ms": round((perf_counter() - started) * 1000, 3),
            "search_ms": reply.search_ms,
            "generation_ms": reply.generation_ms,
            "sources": [source.model_dump(mode="json") for source in reply.sources],
        }
        cursor = await conn.execute(
            insert_message, (session_id, "assistant", reply.text, Jsonb(debug))
        )
        return SendMessageOut(
            session_id=session_id,
            state=state,
            user_message=user_message,
            assistant_message=await cursor.fetchone(),
        )


@router.delete("/{session_id}", status_code=204, response_class=Response)
async def delete_session(session_id: UUID, request: Request):
    async with request.app.state.db_pool.connection() as conn:
        try:
            cursor = await conn.execute(
                "SELECT id FROM sessions WHERE id = %s FOR UPDATE NOWAIT", (session_id,)
            )
        except LockNotAvailable as exc:
            raise HTTPException(status_code=409, detail="session_busy") from exc
        if await cursor.fetchone() is None:
            raise HTTPException(status_code=404, detail="session_not_found")
        await conn.execute(
            "UPDATE sessions SET deleted_at = COALESCE(deleted_at, clock_timestamp()) "
            "WHERE id = %s",
            (session_id,),
        )
    return Response(status_code=204)
