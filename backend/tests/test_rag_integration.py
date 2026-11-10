import asyncio

import pytest

from app.rag import SearchService, debug_sources


def test_search_service_returns_real_fragments_and_isolates_history(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_MODE", "local")
    (tmp_path / "solar.md").write_text(
        "# Солнечная станция\nПанели можно разместить на фасаде здания.\n", encoding="utf-8"
    )
    (tmp_path / "meter.md").write_text(
        "# Учёт\nПлатформа показывает потребление по арендаторам.\n", encoding="utf-8"
    )

    async def run():
        service = await SearchService.create(tmp_path, 2)
        assert service.engine.info["documents"] == 2
        results = await asyncio.gather(
            service.search("А по арендаторам?", [{"role": "user", "content": "Нужен учёт"}]),
            service.search("Солнечные панели на фасаде"),
        )
        assert results[0]["hits"][0]["source"] == "meter.md"
        assert results[1]["hits"][0]["source"] == "solar.md"
        source = debug_sources(results[1])[0]
        assert source["fragment"] in (tmp_path / "solar.md").read_text()
        assert source["fragment_id"] == results[1]["hits"][0]["chunk_id"]
        assert (await service.search("zxqvblorp"))["hits"] == []

    asyncio.run(run())


def test_missing_knowledge_base_fails_explicitly(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_MODE", "local")
    with pytest.raises(FileNotFoundError):
        asyncio.run(SearchService.create(tmp_path / "missing.zip", 1))
