"""Заявка менеджеру с контактами и резюме истории сессии."""

import asyncio
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from psycopg.errors import LockNotAvailable

from app.consultant import ConsultantUnavailable
from app.schemas import LeadIn, LeadOut

router = APIRouter(prefix="/api/sessions", tags=["leads"])


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
async def create_lead(session_id: UUID, body: LeadIn, request: Request, response: Response):
    async with request.app.state.db_pool.connection() as conn:
        try:
            cursor = await conn.execute(
                "SELECT id FROM sessions WHERE id = %s AND deleted_at IS NULL FOR UPDATE NOWAIT",
                (session_id,),
            )
        except LockNotAvailable as exc:
            raise HTTPException(status_code=409, detail="session_busy") from exc
        if await cursor.fetchone() is None:
            raise HTTPException(status_code=404, detail="session_not_found")

        cursor = await conn.execute(
            "SELECT * FROM leads WHERE session_id = %s ORDER BY created_at DESC, id DESC LIMIT 1",
            (session_id,),
        )
        existing = await cursor.fetchone()
        if existing is not None:
            if any(existing[field] != value for field, value in body.model_dump().items()):
                raise HTTPException(status_code=409, detail="lead_already_exists")
            response.status_code = 200
            return LeadOut.model_validate(existing)

        cursor = await conn.execute(
            "SELECT role, text AS content FROM messages WHERE session_id = %s ORDER BY position",
            (session_id,),
        )
        history = await cursor.fetchall()
        if not any(message["role"] == "user" for message in history):
            raise HTTPException(status_code=422, detail="empty_dialog")
        summarizer = request.app.state.lead_summarizer
        if summarizer is None:
            raise HTTPException(status_code=503, detail="consultant_unavailable")
        try:
            async with asyncio.timeout(60):
                summary = await summarizer(history=history)
        except TimeoutError as exc:
            raise HTTPException(status_code=504, detail="consultant_timeout") from exc
        except ConsultantUnavailable as exc:
            raise HTTPException(status_code=503, detail="consultant_unavailable") from exc
        if (
            not isinstance(summary, str)
            or not summary.strip()
            or len(summary) > 20000
            or "\x00" in summary
        ):
            raise HTTPException(status_code=503, detail="consultant_unavailable")

        cursor = await conn.execute(
            "INSERT INTO leads (session_id, full_name, phone, company, summary, created_at) "
            "VALUES (%s, %s, %s, %s, %s, clock_timestamp()) RETURNING *",
            (session_id, body.full_name, body.phone, body.company, summary.strip()),
        )
        lead = LeadOut.model_validate(await cursor.fetchone())
        await conn.execute(
            "UPDATE sessions SET state = 'lead_created' WHERE id = %s", (session_id,)
        )
        return lead
