"""Интерфейс между API сообщений и модулем консультанта."""

from typing import Annotated, Literal, Protocol, TypedDict

from pydantic import BaseModel, Field, FiniteFloat, StringConstraints


class Search(Protocol):
    async def search(self, question: str, history: list[dict] | None = None) -> dict: ...


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
    lead: dict[str, str] | None = None
    lead_summary: str | None = None
    dialog_memory: dict | None = None
    memory_warnings: list[str] = Field(default_factory=list)
    requested_action: Literal["none", "create_lead"] = "none"


class ConsultantUnavailable(Exception):
    """Модель или поиск временно недоступны."""


class ModelRateLimited(ConsultantUnavailable):
    def __init__(self, retry_after: float = 60):
        self.retry_after = max(1, min(3600, retry_after))
        super().__init__("Model rate limit")


class Consultant(Protocol):
    async def __call__(
        self, *, text: str, history: list[HistoryMessage], search: Search
    ) -> ConsultantReply:
        """Принять предыдущие реплики и текущий вопрос, вернуть ответ и метаданные."""
        ...


class LeadSummarizer(Protocol):
    async def __call__(self, *, history: list[HistoryMessage]) -> str:
        """Подготовить резюме заявки только по переданной истории диалога."""
        ...
