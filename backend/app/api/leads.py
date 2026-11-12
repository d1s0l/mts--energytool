"""Заявка менеджеру с контактами и резюме истории сессии."""

import asyncio
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from psycopg.errors import LockNotAvailable

from app.access import require_access
from app.consultant import ConsultantUnavailable
from app.schemas import LeadIn, LeadOut

router = APIRouter(prefix="/api/sessions", tags=["leads"], dependencies=[Depends(require_access)])


@router.get("/{session_id}/lead", response_model=LeadOut)
async def get_lead(session_id: UUID, request: Request, include_deleted: bool = False):
    async with request.app.state.db_pool.connection() as conn:
        await conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        cursor = await conn.execute(
            "SELECT id FROM sessions WHERE id = %s AND (%s OR deleted_at IS NULL)",
            (session_id, include_deleted),
        )
        if await cursor.fetchone() is None:
            raise HTTPException(status_code=404, detail="session_not_found")
        cursor = await conn.execute(
            "SELECT * FROM leads WHERE session_id = %s ORDER BY created_at DESC, id DESC LIMIT 1",
            (session_id,),
        )
        lead = await cursor.fetchone()
        if lead is None:
            raise HTTPException(status_code=404, detail="lead_not_found")
        return lead


@router.post("/{session_id}/lead", status_code=201, response_model=LeadOut)
@router.put("/{session_id}/lead", response_model=LeadOut)
async def create_lead(session_id: UUID, body: LeadIn, request: Request, response: Response):
    token = uuid4()
    pool = request.app.state.db_pool
    async with pool.connection() as conn:
        try:
            cursor = await conn.execute(
                "SELECT id, (processing_token IS NOT NULL AND processing_started_at > "
                "clock_timestamp() - interval '180 seconds') AS busy FROM sessions "
                "WHERE id = %s AND deleted_at IS NULL FOR UPDATE NOWAIT",
                (session_id,),
            )
        except LockNotAvailable as exc:
            raise HTTPException(status_code=409, detail="session_busy") from exc
        session = await cursor.fetchone()
        if session is None:
            raise HTTPException(status_code=404, detail="session_not_found")
        if session["busy"]:
            raise HTTPException(409, "session_busy")

        cursor = await conn.execute(
            "SELECT * FROM leads WHERE session_id = %s ORDER BY created_at DESC, id DESC LIMIT 1",
            (session_id,),
        )
        existing = await cursor.fetchone()
        if existing is not None:
            if request.method == "PUT":
                cursor = await conn.execute(
                    "UPDATE leads SET full_name = %s, phone = %s, company = %s "
                    "WHERE id = %s RETURNING *",
                    (body.full_name, body.phone, body.company, existing["id"]),
                )
                response.status_code = 200
                return LeadOut.model_validate(await cursor.fetchone())
            if any(existing[field] != value for field, value in body.model_dump().items()):
                raise HTTPException(status_code=409, detail="lead_already_exists")
            response.status_code = 200
            return LeadOut.model_validate(existing)

        cursor = await conn.execute(
            "SELECT role, text AS content FROM messages WHERE session_id = %s "
            "AND status = 'completed' ORDER BY position",
            (session_id,),
        )
        history = await cursor.fetchall()
        if not any(message["role"] == "user" for message in history):
            raise HTTPException(status_code=422, detail="empty_dialog")
        summarizer = request.app.state.lead_summarizer
        if summarizer is None:
            raise HTTPException(status_code=503, detail="consultant_unavailable")
        cursor = await conn.execute(
            "UPDATE sessions SET processing_token = %s, processing_started_at = clock_timestamp() "
            "WHERE id = %s AND (processing_token IS NULL OR processing_started_at < "
            "clock_timestamp() - interval '180 seconds') RETURNING id",
            (token, session_id),
        )
        if await cursor.fetchone() is None:
            raise HTTPException(409, "session_busy")
    try:
        async with asyncio.timeout(60):
            summary = await summarizer(history=history)
        if (
            not isinstance(summary, str)
            or not summary.strip()
            or len(summary) > 20000
            or "\x00" in summary
        ):
            raise ConsultantUnavailable()
    except BaseException as exc:

        async def release():
            async with pool.connection() as conn:
                await conn.execute(
                    "UPDATE sessions SET processing_token = NULL, processing_started_at = NULL "
                    "WHERE id = %s AND processing_token = %s",
                    (session_id, token),
                )

        await asyncio.shield(release())
        if isinstance(exc, TimeoutError):
            raise HTTPException(504, "consultant_timeout") from None
        if isinstance(exc, ConsultantUnavailable):
            raise HTTPException(503, "consultant_unavailable") from None
        raise
    async with pool.connection() as conn:
        cursor = await conn.execute(
            "SELECT id FROM sessions WHERE id = %s AND processing_token = %s "
            "AND deleted_at IS NULL FOR UPDATE",
            (session_id, token),
        )
        if await cursor.fetchone() is None:
            raise HTTPException(409, "session_busy")
        cursor = await conn.execute(
            "INSERT INTO leads (session_id, full_name, phone, company, summary, created_at) "
            "VALUES (%s, %s, %s, %s, %s, clock_timestamp()) RETURNING *",
            (session_id, body.full_name, body.phone, body.company, summary.strip()),
        )
        lead = LeadOut.model_validate(await cursor.fetchone())
        await conn.execute(
            "UPDATE sessions SET state = 'lead_created', processing_token = NULL, "
            "processing_started_at = NULL WHERE id = %s",
            (session_id,),
        )
        return lead
