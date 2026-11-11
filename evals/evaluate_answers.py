"""Offline answer comparison. Similarity is not correctness or leaderboard score."""

import argparse
import csv
import html
import json
import re
import sys
from collections import Counter
from pathlib import Path
from statistics import mean


def tokens(text):
    return re.findall(r"\w+", text.casefold().replace("ё", "е"), re.UNICODE)


def f1(overlap, predicted, expected):
    return 2 * overlap / (predicted + expected) if predicted + expected else 0.0


def numbers(text):
    # Join digit groups separated by spaces; preserve decimal points and range ends.
    text = re.sub(r"(?<=\d)[ \u00a0\u202f]+(?=\d{3}(?:\D|$))", "", text)
    return set(re.findall(r"\d+(?:[.,]\d+)?", text.replace(",", ".")))


def similarities(answer, reference):
    a, b = tokens(answer), tokens(reference)
    overlap = sum((Counter(a) & Counter(b)).values())
    # LCS with linear memory. Limit input length before calling this function.
    previous = [0] * (len(b) + 1)
    for word in a:
        current = [0]
        for j, other in enumerate(b, 1):
            current.append(previous[j - 1] + 1 if word == other
                           else max(previous[j], current[-1]))
        previous = current
    return {
        "exact_match": bool(answer.strip()) and answer.strip() == reference.strip(),
        "normalized_match": bool(a) and a == b,
        "token_f1": f1(overlap, len(a), len(b)),
        "rouge_l_f1": f1(previous[-1], len(a), len(b)),
    }


def read_csv(path, column, *, predictions=False):
    rows, issues = {}, []
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        fields = reader.fieldnames or []
        if len(fields) != len(set(fields)) or not {"id", column}.issubset(fields):
            raise ValueError(f"{path}: expected unique columns id,{column}")
        if predictions and fields != ["id", "answer"]:
            issues.append("prediction_columns_must_be_id_answer")
        for line, row in enumerate(reader, 2):
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"{path}:{line}: malformed CSV row")
            qid, text = row["id"].strip(), row[column]
            if not qid or qid in rows:
                raise ValueError(f"{path}:{line}: empty or duplicate ID {qid!r}")
            if not predictions and (not text.strip() or len(text) > 10000 and column == "answer"):
                raise ValueError(f"{path}:{line}: empty or oversized reference/input")
            rows[qid] = text
    if not rows and not predictions:
        raise ValueError(f"{path}: empty input")
    return rows, issues


def evaluate(questions, references, predictions, format_issues=()):
    if set(questions) != set(references):
        raise ValueError("Question and reference IDs differ")
    rows = []
    for qid, question in questions.items():
        reference, answer = references[qid], predictions.get(qid, "")
        flags = []
        if qid not in predictions:
            flags.append("missing_answer")
        elif not answer.strip():
            flags.append("empty_answer")
        if len(answer) > 10000:
            flags.append("over_10000_characters")
        # Invalid oversized answers receive zero metrics; avoid quadratic work on them.
        metrics = similarities(answer, reference) if answer.strip() and len(answer) <= 10000 else {
            "exact_match": False, "normalized_match": False,
            "token_f1": 0.0, "rouge_l_f1": 0.0,
        }
        missing_numbers = sorted(numbers(reference) - numbers(answer))
        extra_numbers = sorted(numbers(answer) - numbers(reference))
        if missing_numbers or extra_numbers:
            flags.append("check_numbers")
        if metrics["token_f1"] < 0.5:
            flags.append("low_lexical_overlap")
        if len(answer.split()) > 150:
            flags.append("over_recommended_150_words")
        if re.search(r"техническ\w* сбой|повторите (?:вопрос|позже)|consultant_unavailable", answer, re.I):
            flags.append("possible_technical_fallback")
        rows.append({"id": qid, "question": question, "reference": reference,
                     "answer": answer, **metrics, "missing_numbers": missing_numbers,
                     "extra_numbers": extra_numbers, "flags": flags})
    missing = sorted(set(questions) - set(predictions))
    extra = sorted(set(predictions) - set(questions))
    valid = not (format_issues or missing or extra) and all(
        row["answer"].strip() and len(row["answer"]) <= 10000 for row in rows
    )
    return {
        "scope": "Demo development set; lexical similarity only. Not semantic correctness or leaderboard Score.",
        "summary": {"questions": len(rows), "prediction_rows": len(predictions),
                    "format_valid": valid, "format_issues": list(format_issues),
                    "missing_ids": missing, "extra_ids": extra,
                    "exact_match_rate": mean(r["exact_match"] for r in rows),
                    "normalized_match_rate": mean(r["normalized_match"] for r in rows),
                    "mean_token_f1": mean(r["token_f1"] for r in rows),
                    "mean_rouge_l_f1": mean(r["rouge_l_f1"] for r in rows),
                    "flagged_answers": sum(bool(r["flags"]) for r in rows)},
        "rows": rows,
    }


def write_reports(report, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with (directory / "details.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(report["rows"][0]))
        writer.writeheader()
        for row in report["rows"]:
            writer.writerow({key: json.dumps(value, ensure_ascii=False) if isinstance(value, list)
                             else value for key, value in row.items()})
    def escape(value):
        return html.escape(str(value))
    cards = []
    for row in sorted(report["rows"], key=lambda item: item["token_f1"]):
        cards.append(
            f'<article><h2>ID {escape(row["id"])} · Token F1 {row["token_f1"]:.1%}'
            f' · ROUGE-L {row["rouge_l_f1"]:.1%}</h2>'
            f'<p class="flags">{escape(", ".join(row["flags"]) or "Нет автоматических замечаний")}</p>'
            f'<h3>Вопрос</h3><pre>{escape(row["question"])}</pre><div class="pair">'
            f'<section><h3>Эталон</h3><pre>{escape(row["reference"])}</pre></section>'
            f'<section><h3>Наш ответ</h3><pre>{escape(row["answer"] or "Ответ отсутствует")}</pre>'
            f'</section></div><p>Числа только в эталоне: {escape(row["missing_numbers"])}; '
            f'только в ответе: {escape(row["extra_numbers"])}</p></article>'
        )
    summary = report["summary"]
    page = '<!doctype html><html lang="ru"><meta charset="utf-8">' \
        '<meta name="viewport" content="width=device-width,initial-scale=1"><title>Answer evals</title>' \
        '<style>body{font:16px system-ui;max-width:1100px;margin:30px auto;padding:0 20px;' \
        'background:#f4f5f7;color:#20232a}article{background:white;padding:20px;margin:20px 0;' \
        'border-radius:12px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit}' \
        '.pair{display:grid;grid-template-columns:1fr 1fr;gap:24px}.flags{color:#9b3500}' \
        '@media(max-width:700px){.pair{grid-template-columns:1fr}}</style>' \
        '<h1>Сравнение ответов</h1><p>Метрики показывают сходство текста, а не правильность ' \
        'по смыслу. Это не оценка LLM-судьи хакатона. Низкое сходство может быть у правильного ' \
        'перефразирования, высокое — у ответа с отрицанием или неверным числом. ' \
        'Флаги и числовые расхождения требуют ручной проверки.</p>' \
        f'<p>Вопросов: {summary["questions"]}. Формат: ' \
        f'{"корректен" if summary["format_valid"] else "есть ошибки"}. ' \
        f'Exact: {summary["exact_match_rate"]:.1%}. Token F1: {summary["mean_token_f1"]:.1%}. ' \
        f'ROUGE-L: {summary["mean_rouge_l_f1"]:.1%}.</p>' \
        f'<pre>{escape(json.dumps(summary, ensure_ascii=False, indent=2))}</pre>' \
        + "".join(cards) + '</html>'
    (directory / "report.html").write_text(page, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", required=True, type=Path)
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument("--predictions", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("evals/reports/latest"))
    args = parser.parse_args()
    try:
        questions, _ = read_csv(args.questions, "question")
        references, _ = read_csv(args.reference, "answer")
        predictions, issues = read_csv(args.predictions, "answer", predictions=True)
        report = evaluate(questions, references, predictions, issues)
        write_reports(report, args.output_dir)
    except (ValueError, OSError, csv.Error) as exc:
        print(f"Evaluation failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"Report: {(args.output_dir / 'report.html').resolve()}")
    return 0 if report["summary"]["format_valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
