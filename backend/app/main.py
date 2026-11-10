"""Точка входа FastAPI. Маршруты добавим после согласования API."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import Settings
from app.db import create_pool
from app.migrate import migrate


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = Settings()
    async with create_pool(settings.database_url) as pool:
        async with pool.connection() as conn:
            await migrate(conn)
        app.state.db_pool = pool
        yield


app = FastAPI(title="MTS Energytool API", lifespan=lifespan)
