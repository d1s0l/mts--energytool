import csv
import tempfile
import unittest
from pathlib import Path

from evals.evaluate_answers import evaluate, numbers, read_csv, similarities, write_reports


class AnswerEvalsTests(unittest.TestCase):
    def test_identical_answers_and_normalization(self):
        self.assertEqual(similarities("Да, всё!", "да все")["token_f1"], 1)
        report = evaluate({"1": "Вопрос"}, {"1": "Да, возможно."}, {"1": "Да, возможно."})
        self.assertEqual(report["summary"]["exact_match_rate"], 1)
        self.assertTrue(report["summary"]["format_valid"])

    def test_ids_join_not_row_order_and_missing_penalized(self):
        report = evaluate({"2": "Второй", "1": "Первый"},
                          {"1": "Ответ один", "2": "Ответ два"},
                          {"1": "Ответ один", "extra": "Лишний"})
        self.assertEqual(report["summary"]["mean_token_f1"], 0.5)
        self.assertEqual(report["summary"]["missing_ids"], ["2"])
        self.assertEqual(report["summary"]["extra_ids"], ["extra"])
        self.assertFalse(report["summary"]["format_valid"])

    def test_repeated_tokens_do_not_inflate_overlap(self):
        self.assertAlmostEqual(similarities("да да да", "да")["token_f1"], 0.5)
        self.assertEqual(similarities("", "")["token_f1"], 0)
        self.assertEqual(similarities("а б", "б а")["rouge_l_f1"], 0.5)

    def test_number_changes_flagged_even_with_high_overlap(self):
        report = evaluate({"1": "Порог?"}, {"1": "Порог от 750 кВт"},
                          {"1": "Порог от 670 кВт"})
        self.assertIn("check_numbers", report["rows"][0]["flags"])
        self.assertEqual(numbers("1 000 000 и 3,5 и 7–9"), {"1000000", "3.5", "7", "9"})

    def test_invalid_empty_and_oversized_predictions(self):
        for answer in ("", "a" * 10001):
            report = evaluate({"1": "?"}, {"1": "Ответ"}, {"1": answer})
            self.assertFalse(report["summary"]["format_valid"])
            self.assertEqual(report["rows"][0]["token_f1"], 0)
        with self.assertRaises(ValueError):
            evaluate({"1": "?"}, {"2": "Ответ"}, {})

    def test_csv_bom_quoting_duplicate_and_html_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(["id", "answer"])
                writer.writerow(["001", 'Да, "так"\n<script>alert(1)</script>'])
            rows, issues = read_csv(path, "answer", predictions=True)
            self.assertEqual(issues, [])
            self.assertIn("001", rows)
            report = evaluate({"001": "?"}, rows, rows)
            write_reports(report, Path(directory) / "report")
            output = (Path(directory) / "report/report.html").read_text()
            self.assertNotIn("<script>", output)
            self.assertIn("&lt;script&gt;", output)
            path.write_text("id,answer\n1,a\n1,b\n")
            with self.assertRaises(ValueError):
                read_csv(path, "answer", predictions=True)


if __name__ == "__main__":
    unittest.main()
