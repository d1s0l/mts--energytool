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
from solution import answer_questions, read_questions, run, split_dialog, write_submission


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
    data = tmp_path / "standalone/data"
    data.mkdir()
    (data / "questions.csv").write_text("id,question\n1,Учёт\n", encoding="utf-8")
    environment["DATA_DIR"] = "data"
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
    assert (tmp_path / "standalone/submission.csv").exists()
    assert '"stage": "load_consultant"' in result.stderr


def test_question_and_checkpoint_failures_do_not_stop_next_question(capsys):
    calls = []
    snapshots = []

    async def consultant(*, text, **kwargs):
        calls.append(text)
        if text == "сбой":
            raise RuntimeError("SECRET-CONTACT-AND-TOKEN")
        return ConsultantReply(text="Ответ")

    def checkpoint(answers):
        snapshots.append(list(answers))
        if len(snapshots) == 1:
            raise OSError(28, "SECRET-PATH")

    answers = asyncio.run(
        answer_questions(
            [("failed-id", "сбой"), ("ok-id", "успех")],
            None,
            consultant,
            1,
            checkpoint=checkpoint,
        )
    )
    assert calls == ["сбой", "успех"]
    assert answers[1] == "Ответ"
    assert len(snapshots) == 2
    logs = capsys.readouterr().err
    assert '"question_id": "failed-id"' in logs
    assert '"stage": "consultant"' in logs
    assert '"stage": "checkpoint"' in logs
    assert '"errno": 28' in logs
    assert "SECRET" not in logs


def test_final_save_retries_and_keeps_successful_answers(tmp_path, monkeypatch):
    import solution

    (tmp_path / "questions.csv").write_text("id,question\n1,Учёт\n", encoding="utf-8")
    monkeypatch.setattr(solution, "KnowledgeSearch", lambda *args: None)
    original = solution.write_submission
    saves = []

    def intermittent_save(path, questions, answers):
        saves.append(list(answers))
        if len(saves) in (2, 3):
            raise PermissionError("temporary failure")
        original(path, questions, answers)

    async def consultant(**kwargs):
        return ConsultantReply(text="Сохранённый ответ")

    monkeypatch.setattr(solution, "write_submission", intermittent_save)
    output = tmp_path / "submission.csv"
    asyncio.run(run(consultant, tmp_path, output, 1))
    assert len(saves) == 4
    with output.open(encoding="utf-8", newline="") as stream:
        assert list(csv.DictReader(stream)) == [{"id": "1", "answer": "Сохранённый ответ"}]


def test_persistent_save_failure_is_reported_and_keeps_previous_csv(tmp_path, monkeypatch):
    import solution

    (tmp_path / "questions.csv").write_text("id,question\n1,Учёт\n", encoding="utf-8")
    monkeypatch.setattr(solution, "KnowledgeSearch", lambda *args: None)
    original = solution.write_submission
    calls = 0

    def failed_save(path, questions, answers):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise OSError(28, "disk full")
        original(path, questions, answers)

    async def consultant(**kwargs):
        return ConsultantReply(text="Ответ")

    monkeypatch.setattr(solution, "write_submission", failed_save)
    output = tmp_path / "submission.csv"
    with pytest.raises(OSError):
        asyncio.run(run(consultant, tmp_path, output, 1))
    with output.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert rows[0]["id"] == "1"
    assert rows[0]["answer"].strip()
    assert calls == 5


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
