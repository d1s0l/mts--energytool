"""Ответы API сессий. UUID и время сериализуются FastAPI."""

import re
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, StringConstraints, field_validator

SessionState = Literal["chatting", "awaiting_contacts", "lead_created"]


class SessionOut(BaseModel):
    access_token: str | None = None
    id: UUID
    state: SessionState
    created_at: datetime
    deleted_at: datetime | None = None


class SessionListItemOut(SessionOut):
    first_message: str | None = None


class SessionListOut(BaseModel):
    items: list[SessionListItemOut]
    total: int
    limit: int
    offset: int


class MessageOut(BaseModel):
    id: UUID
    role: Literal["user", "assistant"]
    text: str
    created_at: datetime
    debug: dict[str, Any] | None
    status: Literal["processing", "completed", "failed"] = "completed"


class SessionDetailOut(SessionOut):
    messages: list[MessageOut]


class SendMessageIn(BaseModel):
    client_message_id: UUID | None = None
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
    lead_id: UUID | None = None


class LeadIn(BaseModel):
    full_name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
    ]
    phone: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
    company: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]

    @field_validator("full_name", "phone", "company")
    @classmethod
    def reject_null_character(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("Поле содержит нулевой символ")
        return value

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, value: str) -> str:
        normalized = re.sub(r"[ ()-]", "", value)
        if not re.fullmatch(r"\+?[0-9]{10,15}", normalized):
            raise ValueError("Введите телефон из 10–15 цифр, допускается + в начале")
        return normalized


class LeadOut(BaseModel):
    full_name: str
    phone: str
    company: str
    id: UUID
    session_id: UUID
    summary: str
    created_at: datetime
