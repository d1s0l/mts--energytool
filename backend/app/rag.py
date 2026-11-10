"""Асинхронная обвязка командного RAG, без изменения его поисковой логики."""

import asyncio
from pathlib import Path

from rag import Config, KnowledgeSearch


class SearchService:
    def __init__(self, engine: KnowledgeSearch, max_workers: int) -> None:
        self.engine = engine
        self._slots = asyncio.Semaphore(max_workers)

    @classmethod
    async def create(cls, path: Path, max_workers: int) -> "SearchService":
        engine = await asyncio.to_thread(KnowledgeSearch, path, Config.from_env())
        return cls(engine, max_workers)

    async def search(self, question: str, history: list[dict] | None = None) -> dict:
        async with self._slots:
            # При отмене HTTP-запроса поток продолжает работу. Не освобождаем слот,
            # пока поиск действительно не завершится.
            task = asyncio.create_task(
                asyncio.to_thread(self.engine.search, question, history=history)
            )
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                try:
                    await task
                finally:
                    raise


def debug_sources(result: dict) -> list[dict]:
    return [
        {
            "document_id": hit["source"],
            "title": hit["title"],
            "fragment_id": hit["chunk_id"],
            "fragment": hit["text"],
            "score": hit["score"],
        }
        for hit in result["hits"]
    ]
