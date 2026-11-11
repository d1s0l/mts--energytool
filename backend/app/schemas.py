"""Ответы API сессий. UUID и время сериализуются FastAPI."""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, StringConstraints, field_validator

SessionState = Literal["chatting", "awaiting_contacts", "lead_created"]


class SessionOut(BaseModel):
    id: UUID
    state: SessionState
    created_at: datetime
    deleted_at: datetime | None = None


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


class SendMessageIn(BaseModel):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50000)]

    @field_validator("text")
    @classmethod
    def reject_null_character(cls, text: str) -> str:
        if "\x00" in text:
            raise ValueError("Текст содержит нулевой символ")
        return text


class SendMessageOut(BaseModel):
    session_id: UUID
    state: SessionState
    user_message: MessageOut
    assistant_message: MessageOut
