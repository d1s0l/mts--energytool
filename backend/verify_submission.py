"""Build and audit the flat ZIP; optionally execute its real entry point with the model."""

import argparse
import ast
import csv
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from zipfile import ZipFile

from build_submission import build_submission
from evaluate_answers import save
from solution import read_questions


CONTACT_CASES = [
    (
        "lead-complete",
        "Пользователь: Нужен учёт на трёх складах, 45 счётчиков.\nПользователь: Оформите заявку: Тест Тестов, компания Альфа, телефон +79990000000.",
    ),
    (
        "lead-refused",
        "Пользователь: Нужен учёт на трёх складах, 45 счётчиков.\nПользователь: Я Тест Тестов, компания Альфа, телефон +79990000000.\nПользователь: Не звоните, заявку не оформляйте. Объясните, какие данные нужны для оценки.",
    ),
    (
        "lead-partial",
        "Пользователь: Нужен учёт на складах.\nПользователь: Хочу оформить заявку, я Тест Тестов, компания Альфа. Телефон пока не сообщал.",
    ),
    (
        "lead-correction",
        "Пользователь: Нужен учёт на трёх складах, 45 счётчиков.\nПользователь: Оформите заявку: Тест Тестов, компания Альфа, телефон +79990000000.\nПользователь: Исправление: телефон для заявки +79991111111, замените прежний номер.",
    ),
]


def audit_archive(path, approved=None):
    with ZipFile(path) as z:
        names = z.namelist()
        expected = {
            "solution.py",
            "consultant.py",
            "consultant_impl.py",
            "dialog_memory.py",
            "rag_search.py",
            "requirements.txt",
        }
        if set(names) != expected or len(names) != len(expected):
            raise ValueError("Archive must contain only the expected root modules and requirements")
        if path.stat().st_size > 128 * 1024 * 1024:
            raise ValueError("Archive exceeds 128 MiB")
        for n in names:
            if n.endswith(".py"):
                ast.parse(z.read(n), filename=n)
        requirements = [
            r.strip()
            for r in z.read("requirements.txt").decode().splitlines()
            if r.strip() and not r.startswith("#")
        ]
        if any("==" not in r for r in requirements):
            raise ValueError("Submission dependencies must use exact versions")
        if approved:
            allowed = set(approved.read_text().splitlines())
            if not set(requirements) <= allowed:
                raise ValueError("Submission uses a dependency outside the supplied allowlist")
    return {
        "status": "passed",
        "files": names,
        "bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "requirements": requirements,
    }


def validate_csv(path, questions):
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != ["id", "answer"]:
            raise ValueError("Invalid submission CSV columns")
        rows = list(reader)
    if [r["id"] for r in rows] != [qid for qid, _ in questions]:
        raise ValueError("Submission IDs, order or row count do not match input")
    for row in rows:
        answer = row["answer"]
        if not answer.strip() or len(answer) > 10000:
            raise ValueError("Invalid answer size")
        if "технического сбоя" in answer or "Временный технический сбой" in answer:
            raise ValueError("A technical fallback is not a successful model answer")
        if row["id"] in {"lead-complete", "lead-correction"}:
            expected = "+79990000000" if row["id"] == "lead-complete" else "+79991111111"
            if not all(
                s in answer for s in ("Заявка для менеджера", "Тест Тестов", "Альфа", expected)
            ):
                raise ValueError("Submission did not form the expected lead")
            if row["id"] == "lead-correction" and "+79990000000" in answer:
                raise ValueError("Submission resurrected the old phone number")
        if row["id"] in {"lead-refused", "lead-partial"} and "Заявка для менеджера" in answer:
            raise ValueError("Submission created a lead without consent or complete contacts")
    return rows


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--env", type=Path)
    p.add_argument("--kb", type=Path, required=True)
    p.add_argument("--questions", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--archive", type=Path, required=True)
    p.add_argument("--approved-requirements", type=Path)
    p.add_argument("--audit-only", action="store_true")
    p.add_argument("--interval", type=float, default=0)
    p.add_argument("--question-interval", type=float, default=45)
    p.add_argument(
        "--python",
        dest="runtime_python",
        default=sys.executable,
        help="Interpreter of a clean submission environment",
    )
    args = p.parse_args()
    build_submission(args.archive)
    report = {"status": "running", "entry_point": "python solution.py", "transport_mocked": False}
    try:
        report["archive"] = audit_archive(args.archive, args.approved_requirements)
        if args.audit_only:
            report["status"] = "audit_only"
            return 0
        env = os.environ.copy()
        if args.env:
            from dotenv import dotenv_values

            env.update({k: v for k, v in dotenv_values(args.env).items() if v is not None})
        for name in ("PYTHONPATH", "TEST_DATABASE_URL", "DATABASE_URL", "API_ACCESS_TOKEN"):
            env.pop(name, None)
        env.update(
            MAX_WORKERS="1",
            LLM_MIN_INTERVAL=str(args.interval),
            SUBMISSION_TIMEOUT="1100",
            SUBMISSION_MIN_INTERVAL=str(args.question_interval),
        )
        report["execution_profile"] = {
            "MAX_WORKERS": 1,
            "LLM_MIN_INTERVAL": args.interval,
            "SUBMISSION_TIMEOUT": 1100,
            "SUBMISSION_MIN_INTERVAL": args.question_interval,
        }
        version_probe = subprocess.run(
            [
                args.runtime_python,
                "-c",
                "import importlib.metadata,json; print(json.dumps({n:importlib.metadata.version(n) for n in ('pydantic','httpx')}))",
            ],
            env=env,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        import json

        report["runtime_packages"] = json.loads(version_probe.stdout)
        questions = read_questions(args.questions) + CONTACT_CASES
        with tempfile.TemporaryDirectory(prefix="energytool-submit-") as temp:
            root = Path(temp)
            with ZipFile(args.archive) as z:
                z.extractall(root)
            data = root / "data"
            data.mkdir()
            shutil.copyfile(args.kb, data / "knowledge-base.zip")
            with (data / "questions.csv").open("w", encoding="utf-8", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["id", "question"])
                writer.writerows(questions)
            env["DATA_DIR"] = str(data)
            result = subprocess.run(
                [args.runtime_python, "solution.py"],
                cwd=root,
                env=env,
                text=True,
                capture_output=True,
                timeout=1180,
            )
            report["returncode"] = result.returncode
            # The entry point emits static errors only; never persist provider exception bodies.
            report["stderr_types"] = [
                line
                for line in result.stderr.splitlines()
                if line.startswith(("Question ", "Submission failed ("))
            ]
            output = root / "submission.csv"
            if output.exists():
                shutil.copyfile(output, args.output.with_suffix(".csv"))
            if result.returncode:
                raise ValueError("Submission process failed")
            report["answers"] = validate_csv(output, questions)
            report["question_count"] = len(questions)
            report["status"] = "complete"
    except Exception as exc:
        report.update(status="failed", error_type=type(exc).__name__)
        if type(exc) is ValueError:
            report["check"] = str(exc)
        return 1
    finally:
        save(args.output, report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
