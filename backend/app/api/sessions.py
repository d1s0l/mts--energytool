"""Сессии, история и отправка сообщений."""

import asyncio
import secrets
from time import perf_counter
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from psycopg.errors import LockNotAvailable
from psycopg.types.json import Jsonb

from app.access import require_access, token_hash
from app.consultant import ConsultantReply, ConsultantUnavailable, ModelRateLimited
from app.schemas import (
    LeadIn,
    SendMessageIn,
    SendMessageOut,
    SessionDetailOut,
    SessionListOut,
    SessionOut,
)

router = APIRouter(
    prefix="/api/sessions", tags=["sessions"], dependencies=[Depends(require_access)]
)


@router.post("", status_code=201, response_model=SessionOut)
async def create_session(request: Request):
    token = secrets.token_urlsafe(32)
    async with request.app.state.db_pool.connection() as conn:
        cursor = await conn.execute(
            "INSERT INTO sessions(access_token_hash) VALUES (%s) RETURNING *", (token_hash(token),)
        )
        return {**await cursor.fetchone(), "access_token": token}


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
            "SELECT id, role, text, created_at, debug, status FROM messages "
            "WHERE session_id = %s ORDER BY position",
            (session_id,),
        )
        return {**session, "messages": await cursor.fetchall()}


@router.post("/{session_id}/messages", response_model=SendMessageOut)
async def send_message(session_id: UUID, body: SendMessageIn, request: Request):
    started = perf_counter()
    pool = request.app.state.db_pool
    request_id = body.client_message_id or uuid4()
    token = uuid4()
    # Accept and persist the question before calling the external service.
    async with pool.connection() as conn:
        try:
            cursor = await conn.execute(
                "SELECT * FROM sessions WHERE id = %s AND deleted_at IS NULL FOR UPDATE NOWAIT",
                (session_id,),
            )
        except LockNotAvailable as exc:
            raise HTTPException(409, "session_busy") from exc
        session = await cursor.fetchone()
        if session is None:
            raise HTTPException(404, "session_not_found")
        if body.client_message_id is None:
            # Старые клиенты повторяют неудачный ход без идентификатора запроса.
            cursor = await conn.execute(
                "SELECT role, text, status, request_id FROM messages "
                "WHERE session_id = %s ORDER BY position DESC LIMIT 1",
                (session_id,),
            )
            latest = await cursor.fetchone()
            if (
                latest
                and latest["role"] == "user"
                and latest["status"] == "failed"
                and latest["text"] == body.text
                and latest["request_id"] is not None
            ):
                request_id = latest["request_id"]
        cursor = await conn.execute(
            "SELECT * FROM messages WHERE session_id = %s AND request_id = %s ORDER BY position",
            (session_id, request_id),
        )
        previous = await cursor.fetchall()
        if previous and previous[0]["text"] != body.text:
            raise HTTPException(409, "request_conflict")
        if len(previous) == 2 and previous[1]["status"] == "completed":
            return SendMessageOut(
                session_id=session_id,
                state=session["state"],
                user_message=previous[0],
                assistant_message=previous[1],
                lead_id=(previous[1]["debug"] or {}).get("lead_id"),
            )
        cursor = await conn.execute(
            "UPDATE sessions SET processing_token = %s, processing_started_at = clock_timestamp() "
            "WHERE id = %s AND (processing_token IS NULL OR "
            "processing_started_at < clock_timestamp() - interval '180 seconds') RETURNING id",
            (token, session_id),
        )
        if await cursor.fetchone() is None:
            raise HTTPException(409, "session_busy")
        if previous:
            cursor = await conn.execute(
                "UPDATE messages SET status = 'processing' WHERE id = %s RETURNING *",
                (previous[0]["id"],),
            )
        else:
            cursor = await conn.execute(
                "INSERT INTO messages (session_id, role, text, request_id, status) "
                "VALUES (%s, 'user', %s, %s, 'processing') RETURNING *",
                (session_id, body.text, request_id),
            )
        user_message = await cursor.fetchone()
        cursor = await conn.execute(
            "SELECT role, text AS content FROM messages WHERE session_id = %s "
            "AND status = 'completed' ORDER BY position",
            (session_id,),
        )
        history = await cursor.fetchall()
    try:
        consultant = request.app.state.consultant
        if consultant is None:
            raise ConsultantUnavailable()
        async with asyncio.timeout(60):
            reply = await consultant(
                text=body.text, history=history, search=request.app.state.search
            )
        # Apply schema validation even to third-party implementations.
        reply = ConsultantReply.model_validate(reply)
        async with pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT state FROM sessions WHERE id = %s AND processing_token = %s "
                "AND deleted_at IS NULL FOR UPDATE",
                (session_id, token),
            )
            current = await cursor.fetchone()
            if current is None:
                raise HTTPException(409, "session_busy")
            lead_id = None
            if reply.lead is not None:
                contacts = LeadIn.model_validate(reply.lead)
                if not reply.lead_summary or not reply.lead_summary.strip():
                    raise ConsultantUnavailable()
                cursor = await conn.execute(
                    "SELECT id FROM leads WHERE session_id = %s ORDER BY created_at DESC LIMIT 1",
                    (session_id,),
                )
                existing = await cursor.fetchone()
                if existing:
                    lead_id = existing["id"]
                    await conn.execute(
                        "UPDATE leads SET full_name = %s, phone = %s, company = %s, summary = %s "
                        "WHERE id = %s",
                        (
                            contacts.full_name,
                            contacts.phone,
                            contacts.company,
                            reply.lead_summary,
                            lead_id,
                        ),
                    )
                else:
                    cursor = await conn.execute(
                        "INSERT INTO leads(session_id, full_name, phone, company, summary) "
                        "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                        (
                            session_id,
                            contacts.full_name,
                            contacts.phone,
                            contacts.company,
                            reply.lead_summary,
                        ),
                    )
                    lead_id = (await cursor.fetchone())["id"]
                reply.text += "\n\nЗаявка сохранена в прототипе."
            state = "lead_created" if lead_id or current["state"] == "lead_created" else reply.state
            debug = {
                "total_ms": round((perf_counter() - started) * 1000, 3),
                "lead_id": str(lead_id) if lead_id else None,
                "search_ms": reply.search_ms,
                "generation_ms": reply.generation_ms,
                "sources": [source.model_dump(mode="json") for source in reply.sources],
            }
            cursor = await conn.execute(
                "INSERT INTO messages(session_id, role, text, debug, request_id) "
                "VALUES (%s, 'assistant', %s, %s, %s) RETURNING *",
                (session_id, reply.text, Jsonb(debug), request_id),
            )
            assistant_message = await cursor.fetchone()
            await conn.execute(
                "UPDATE messages SET status = 'completed' WHERE id = %s", (user_message["id"],)
            )
            await conn.execute(
                "UPDATE sessions SET state = %s, processing_token = NULL, "
                "processing_started_at = NULL WHERE id = %s",
                (state, session_id),
            )
            user_message["status"] = "completed"
            return SendMessageOut(
                session_id=session_id,
                state=state,
                user_message=user_message,
                assistant_message=assistant_message,
                lead_id=lead_id,
            )
    except BaseException as exc:
        # A failed/cancelled turn stays inspectable and can be retried with the same ID.
        async def cleanup():
            async with pool.connection() as conn:
                cursor = await conn.execute(
                    "UPDATE sessions SET processing_token = NULL, processing_started_at = NULL "
                    "WHERE id = %s AND processing_token = %s RETURNING id",
                    (session_id, token),
                )
                if await cursor.fetchone():
                    await conn.execute(
                        "UPDATE messages SET status = 'failed' WHERE id = %s", (user_message["id"],)
                    )

        await asyncio.shield(cleanup())
        if isinstance(exc, TimeoutError):
            raise HTTPException(504, "consultant_timeout") from None
        if isinstance(exc, ModelRateLimited):
            raise HTTPException(429, "model_rate_limited",
                                headers={"Retry-After": str(max(1, int(exc.retry_after)))}) from None
        if isinstance(exc, ConsultantUnavailable):
            raise HTTPException(503, "consultant_unavailable") from None
        raise


@router.delete("/{session_id}", status_code=204, response_class=Response)
async def delete_session(session_id: UUID, request: Request):
    async with request.app.state.db_pool.connection() as conn:
        try:
            cursor = await conn.execute(
                "SELECT id, (processing_token IS NOT NULL AND processing_started_at > "
                "clock_timestamp() - interval '180 seconds') AS busy "
                "FROM sessions WHERE id = %s FOR UPDATE NOWAIT",
                (session_id,),
            )
        except LockNotAvailable as exc:
            raise HTTPException(status_code=409, detail="session_busy") from exc
        session = await cursor.fetchone()
        if session is None:
            raise HTTPException(status_code=404, detail="session_not_found")
        if session["busy"]:
            raise HTTPException(409, "session_busy")
        await conn.execute(
            "UPDATE sessions SET deleted_at = COALESCE(deleted_at, clock_timestamp()) "
            "WHERE id = %s",
            (session_id,),
        )
    return Response(status_code=204)
