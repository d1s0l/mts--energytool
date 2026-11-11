import asyncio
import csv
import os
import subprocess
import sys
from zipfile import ZipFile

import pytest
from rag.rag_search import Config, KnowledgeSearch

from app.consultant import ConsultantReply
from build_submission import build_submission
from solution import answer_questions, read_questions, split_dialog, write_submission


@pytest.mark.parametrize("separator", ["\n", r"\n"])
def test_dialog_separates_last_question_from_history(separator):
    question = separator.join(
        ["Пользователь: Три склада", "Ассистент: Какая задача?", "Пользователь: Учёт"]
    )
    text, history = split_dialog(question)
    assert text == "Учёт"
    assert history == [
        {"role": "user", "content": "Три склада"},
        {"role": "assistant", "content": "Какая задача?"},
    ]
    assert split_dialog("Обычный вопрос") == ("Обычный вопрос", [])


def test_parallel_answers_preserve_ids_csv_and_isolation(tmp_path):
    (tmp_path / "kb.md").write_text("# Учёт\nУчёт по складам.\n", encoding="utf-8")
    engine = KnowledgeSearch(tmp_path, Config(mode="local"))
    questions = [("2", "Первый"), ("1", "Пользователь: Склады\nПользователь: Учёт")]

    async def consultant(*, text, history, search):
        if text == "Первый":
            assert history == []
            await asyncio.sleep(0.01)
        else:
            assert history == [{"role": "user", "content": "Склады"}]
            assert (await search.search(text, history=history))["hits"]
        return ConsultantReply(text=f'{text}, "ответ"\nвторая строка')

    answers = asyncio.run(answer_questions(questions, engine, consultant, 2))
    output = tmp_path / "submission.csv"
    write_submission(output, questions, answers)
    with output.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert [row["id"] for row in rows] == ["2", "1"]
    assert [row["answer"] for row in rows] == answers

    async def too_long(**kwargs):
        return ConsultantReply(text="x" * 10001)

    failed = asyncio.run(answer_questions(questions, engine, too_long, 1))
    assert all("технического сбоя" in answer for answer in failed)
    # Неуспешная обработка не заменяет уже записанный файл частичными ответами.
    with output.open(newline="", encoding="utf-8") as stream:
        assert list(csv.DictReader(stream)) == rows


def test_input_rejects_duplicate_ids_and_accepts_bom(tmp_path):
    path = tmp_path / "questions.csv"
    path.write_text("id,question\n1,Учёт\n", encoding="utf-8-sig")
    assert read_questions(path) == [("1", "Учёт")]
    path.write_text("id,question\n1,Учёт\n1,Склады\n", encoding="utf-8")
    with pytest.raises(ValueError):
        read_questions(path)


def test_archive_runs_without_web_backend_and_fails_explicitly_without_model(tmp_path):
    archive = tmp_path / "submission.zip"
    assert build_submission(archive)
    with ZipFile(archive) as zipped:
        assert set(zipped.namelist()) == {
            "solution.py",
            "consultant.py",
            "consultant_impl.py",
            "rag_search.py",
            "dialog_memory.py",
            "requirements.txt",
        }
        zipped.extractall(tmp_path / "standalone")
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    for name in (
        "LLM_BASE_URL",
        "OPENAI_BASE_URL",
        "LLM_API_KEY",
        "OPENAI_API_KEY",
        "LLM_MODEL",
        "OPENAI_MODEL",
        "GEN_MODEL",
    ):
        environment.pop(name, None)
    result = subprocess.run(
        [sys.executable, "solution.py"],
        cwd=tmp_path / "standalone",
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 1
    assert "LLM_BASE_URL" in result.stderr
    assert not (tmp_path / "standalone/submission.csv").exists()


def test_packaged_entry_point_reads_data_and_writes_csv(tmp_path):
    implementation = tmp_path / "implementation.py"
    implementation.write_text(
        "from consultant import ConsultantReply\n"
        "def create_consultant():\n"
        "    async def answer(*, text, history, search):\n"
        "        assert not history\n"
        "        result = await search.search(text, history=history)\n"
        "        assert result['hits']\n"
        "        return ConsultantReply(text='Проверка CSV')\n"
        "    return answer\n",
        encoding="utf-8",
    )
    archive = tmp_path / "submission.zip"
    assert build_submission(archive, implementation)
    standalone = tmp_path / "standalone"
    with ZipFile(archive) as zipped:
        zipped.extractall(standalone)
    data = standalone / "data"
    data.mkdir()
    (data / "questions.csv").write_text("id,question\n42,Учёт складов\n", encoding="utf-8")
    with ZipFile(data / "knowledge-base.zip", "w") as zipped:
        zipped.writestr("meter.md", "# Учёт\nУчёт потребления по складам.")
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment.update(DATA_DIR="data", RAG_MODE="local", MAX_WORKERS="2")
    result = subprocess.run(
        [sys.executable, "solution.py"],
        cwd=standalone,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    with (standalone / "submission.csv").open(encoding="utf-8", newline="") as stream:
        assert list(csv.DictReader(stream)) == [{"id": "42", "answer": "Проверка CSV"}]
