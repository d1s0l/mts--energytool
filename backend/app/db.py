"""Пул подключений PostgreSQL. Схема данных пока не определена."""

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool


def create_pool(url: str) -> AsyncConnectionPool:
    return AsyncConnectionPool(
        url,
        open=False,
        min_size=1,
        max_size=10,
        kwargs={"row_factory": dict_row},
    )
