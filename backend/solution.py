"""CSV-адаптер платформы. Один индекс RAG, независимая история каждого ID."""

import asyncio
import csv
import importlib
import os
import re
import sys
import tempfile
import time
from collections import deque
from pathlib import Path

try:
    from consultant import (
        Consultant,
        ConsultantReply,
        ConsultantUnavailable,
        HistoryMessage,
        ModelRateLimited,
    )
except ModuleNotFoundError as exc:
    if exc.name != "consultant":
        raise
    from app.consultant import (
        Consultant,
        ConsultantReply,
        ConsultantUnavailable,
        HistoryMessage,
        ModelRateLimited,
    )

try:
    from rag_search import Config, KnowledgeSearch
except ModuleNotFoundError as exc:
    if exc.name != "rag_search":
        raise
    from rag.rag_search import Config, KnowledgeSearch


def read_questions(path: Path) -> list[tuple[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not {"id", "question"}.issubset(reader.fieldnames or []):
            raise ValueError("Input CSV must contain id and question")
        rows = []
        ids = set()
        for row in reader:
            qid = (row.get("id") or "").strip()
            question = row.get("question") or ""
            if not qid or qid in ids or not question.strip():
                raise ValueError("Input contains an empty or duplicate ID, or an empty question")
            ids.add(qid)
            rows.append((qid, question))
        if not rows:
            raise ValueError("Input CSV contains no questions")
        return rows


def split_dialog(question: str) -> tuple[str, list[HistoryMessage]]:
    # Демо содержит как реальные переводы строк, так и буквальные \\n перед маркерами.
    normalized = re.sub(r"\\n(?=\s*(?:Пользователь|Ассистент):)", "\n", question)
    markers = list(re.finditer(r"(?:^|\n)\s*(Пользователь|Ассистент):\s*", normalized))
    if not markers or normalized[: markers[0].start()].strip():
        return question.strip(), []
    messages: list[HistoryMessage] = []
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(normalized)
        content = normalized[marker.end() : end].strip()
        if not content:
            raise ValueError("Dialog contains an empty message")
        messages.append(
            {"role": "user" if marker[1] == "Пользователь" else "assistant", "content": content}
        )
    if messages[-1]["role"] != "user":
        raise ValueError("Dialog must end with a user message")
    return messages[-1]["content"], messages[:-1]


class SubmissionSearch:
    def __init__(self, engine: KnowledgeSearch):
        self.engine = engine

    async def search(self, question: str, history: list[dict] | None = None) -> dict:
        return await asyncio.to_thread(self.engine.search, question, history=history)


async def answer_questions(
    questions: list[tuple[str, str]],
    engine: KnowledgeSearch,
    consultant: Consultant,
    max_workers: int,
    *,
    deadline: float | None = None,
    checkpoint=None,
) -> list[str]:
    if not 1 <= max_workers <= 8:
        raise ValueError("MAX_WORKERS must be between 1 and 8")
    pending = deque(enumerate(questions))
    fallback = (
        "Не удалось получить ответ из-за временного технического сбоя. Попробуйте повторить вопрос."
    )
    answers = [fallback] * len(questions)
    search = SubmissionSearch(engine)
    rate_limit_retries = {}
    adaptive_interval = float(os.getenv("SUBMISSION_MIN_INTERVAL", "0"))
    if not 0 <= adaptive_interval <= 60:
        raise ValueError("SUBMISSION_MIN_INTERVAL must be between 0 and 60")
    next_turn_at = 0.0
    dispatch_lock = asyncio.Lock()

    async def reserve_turn():
        nonlocal next_turn_at
        async with dispatch_lock:
            while next_turn_at > time.monotonic():
                if deadline is not None and next_turn_at >= deadline:
                    return False
                await asyncio.sleep(next_turn_at - time.monotonic())
            next_turn_at = time.monotonic() + adaptive_interval
            return True

    async def worker():
        nonlocal adaptive_interval, next_turn_at
        while pending:
            index, (_, question) = pending.popleft()
            if not await reserve_turn():
                return
            remaining = 60 if deadline is None else min(60, deadline - time.monotonic())
            if remaining <= 0:
                return
            try:
                text, history = split_dialog(question)
                async with asyncio.timeout(remaining):
                    reply = ConsultantReply.model_validate(
                        await consultant(text=text, history=history, search=search)
                    )
                if reply.lead is not None:
                    reply.text += (
                        "\n\nЗаявка для менеджера (тестовые данные):\n"
                        + "\n".join(
                            f"{label}: {reply.lead.get(key, '')}"
                            for key, label in (
                                ("full_name", "ФИО"),
                                ("phone", "Телефон"),
                                ("company", "Компания"),
                            )
                        )
                        + "\n"
                        + (reply.lead_summary or "")
                    )
                if len(reply.text) > 10000:
                    raise ValueError("An answer exceeds the platform limit of 10000 characters")
                answers[index] = reply.text
            except ModelRateLimited as exc:
                retries = rate_limit_retries.get(index, 0)
                delay = max(60, exc.retry_after)
                adaptive_interval = max(45, adaptive_interval)
                next_turn_at = max(next_turn_at, time.monotonic() + delay)
                can_retry = retries < 2 and (
                    deadline is None or deadline - time.monotonic() > delay + 55
                )
                if can_retry:
                    rate_limit_retries[index] = retries + 1
                    print(
                        f"Question {index + 1} rate limited; waiting before retry", file=sys.stderr
                    )
                    pending.appendleft((index, questions[index]))
                else:
                    print(f"Question {index + 1} failed (ModelRateLimited)", file=sys.stderr)
            except Exception as exc:
                # Never emit provider exception text: it may contain secrets or contacts.
                print(f"Question {index + 1} failed ({type(exc).__name__})", file=sys.stderr)
            if checkpoint is not None:
                checkpoint(answers)

    tasks = [asyncio.create_task(worker()) for _ in range(min(max_workers, len(questions)))]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    return answers


def write_submission(path: Path, questions: list[tuple[str, str]], answers: list[str]) -> None:
    if len(questions) != len(answers) or any(not answer.strip() for answer in answers):
        raise ValueError("Each question must have one nonempty answer")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", dir=path.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            writer = csv.writer(stream)
            writer.writerow(["id", "answer"])
            writer.writerows(
                (qid, answer) for (qid, _), answer in zip(questions, answers, strict=True)
            )
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_consultant() -> Consultant:
    try:
        module = importlib.import_module("consultant_impl")
    except ModuleNotFoundError as exc:
        if exc.name != "consultant_impl":
            raise
        raise RuntimeError(
            "Add consultant_impl.py with create_consultant() before submitting"
        ) from exc
    consultant = module.create_consultant()
    if not callable(consultant):
        raise TypeError("create_consultant() must return an async callable")
    return consultant


async def run(consultant: Consultant, data_dir: Path, output: Path, max_workers: int) -> None:
    if not 1 <= max_workers <= 8:
        raise ValueError("MAX_WORKERS must be between 1 and 8")
    questions = read_questions(data_dir / "questions.csv")
    deadline = time.monotonic() + min(1120, max(1, float(os.getenv("SUBMISSION_TIMEOUT", "1100"))))
    fallback = ["Временный технический сбой. Повторите вопрос." for _ in questions]
    write_submission(output, questions, fallback)
    engine = await asyncio.to_thread(
        KnowledgeSearch, data_dir / "knowledge-base.zip", Config.from_env()
    )
    answers = await answer_questions(
        questions,
        engine,
        consultant,
        max_workers,
        deadline=deadline,
        checkpoint=lambda completed: write_submission(output, questions, completed),
    )
    write_submission(output, questions, answers)


def main() -> int:
    try:
        consultant = load_consultant()
        asyncio.run(
            run(
                consultant,
                Path(os.getenv("DATA_DIR", "data")),
                Path("submission.csv"),
                int(os.getenv("MAX_WORKERS", "3")),
            )
        )
    except Exception as exc:
        # Текст исключения провайдера может содержать ключ или пользовательские данные.
        print(f"Submission failed ({type(exc).__name__})", file=sys.stderr)
        if isinstance(exc, ModuleNotFoundError) or (
            isinstance(exc, RuntimeError) and "consultant_impl.py" in str(exc)
        ):
            print("Connect consultant_impl.py exporting create_consultant()", file=sys.stderr)
        if isinstance(exc, ConsultantUnavailable):
            print("Configure LLM_BASE_URL, LLM_API_KEY and LLM_MODEL", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
