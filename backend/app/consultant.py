"""Интерфейс между API сообщений и модулем консультанта."""

from typing import Annotated, Literal, Protocol, TypedDict

from pydantic import BaseModel, Field, FiniteFloat, StringConstraints

from app.rag import SearchService


class HistoryMessage(TypedDict):
    role: Literal["user", "assistant"]
    content: str


class DebugSource(BaseModel):
    document_id: str
    title: str
    fragment_id: str
    fragment: str
    score: FiniteFloat | None = None


class ConsultantReply(BaseModel):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    state: Literal["chatting", "awaiting_contacts"] = "chatting"
    search_ms: Annotated[FiniteFloat, Field(ge=0)] | None = None
    generation_ms: Annotated[FiniteFloat, Field(ge=0)] | None = None
    sources: list[DebugSource] = Field(default_factory=list)


class ConsultantUnavailable(Exception):
    """Модель или поиск временно недоступны."""


class Consultant(Protocol):
    async def __call__(
        self, *, text: str, history: list[HistoryMessage], search: SearchService
    ) -> ConsultantReply:
        """Принять предыдущие реплики и текущий вопрос, вернуть ответ и метаданные."""
        ...


class LeadSummarizer(Protocol):
    async def __call__(self, *, history: list[HistoryMessage]) -> str:
        """Подготовить резюме заявки только по переданной истории диалога."""
        ...
