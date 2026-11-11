"""FastAPI: подключение PostgreSQL, построение RAG и маршруты сессий."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI

from app.api.leads import router as leads_router
from app.api.sessions import router
from app.config import Settings
from app.consultant import Consultant, LeadSummarizer
from app.db import create_pool
from app.errors import register_errors
from app.migrate import migrate
from app.rag import SearchService


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = app.state.settings
    async with create_pool(settings.database_url) as pool:
        async with pool.connection() as conn:
            await migrate(conn)
        app.state.db_pool = pool
        app.state.search = await SearchService.create(
            settings.knowledge_base_path, settings.max_workers
        )
        yield


def create_app(
    settings: Settings | None = None,
    *,
    consultant: Consultant | None = None,
    lead_summarizer: LeadSummarizer | None = None,
) -> FastAPI:
    load_dotenv(override=False)
    application = FastAPI(title="MTS Energytool API", lifespan=lifespan)
    application.state.settings = settings or Settings()
    application.state.consultant = consultant
    application.state.lead_summarizer = lead_summarizer
    register_errors(application)
    application.include_router(router)
    application.include_router(leads_router)
    return application


app = create_app()
