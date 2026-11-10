"""Ответы API сессий. UUID и время сериализуются FastAPI."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel

SessionState = Literal["chatting", "awaiting_contacts", "lead_created"]


class SessionOut(BaseModel):
    id: UUID
    state: SessionState
    created_at: datetime


class SessionListOut(BaseModel):
    items: list[SessionOut]
    total: int
    limit: int
    offset: int


class MessageOut(BaseModel):
    id: UUID
    role: Literal["user", "assistant"]
    text: str
    created_at: datetime
    debug: dict[str, Any] | None


class SessionDetailOut(SessionOut):
    messages: list[MessageOut]
