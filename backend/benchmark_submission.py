"""Compare real isolated ZIP runs; credentials are inherited, never written to reports."""

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from zipfile import ZipFile

from build_submission import build_submission


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--count", type=int, default=8)
    parser.add_argument("--workers", type=int, nargs="+", default=[3, 6, 8])
    args = parser.parse_args()
    if args.count < 1 or any(not 1 <= n <= 8 for n in args.workers):
        parser.error("count must be positive; workers must be 1..8")
    args.output.mkdir(parents=True, exist_ok=True)
    results = []
    with (args.data_dir / "questions.csv").open(encoding="utf-8-sig", newline="") as stream:
        questions = list(csv.DictReader(stream))[: args.count]
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        archive = root / "submission.zip"
        build_submission(archive)
        for workers in args.workers:
            work = root / str(workers)
            work.mkdir()
            with ZipFile(archive) as zipped:
                zipped.extractall(work)
            data = work / "data"
            data.mkdir()
            shutil.copyfile(args.data_dir / "knowledge-base.zip", data / "knowledge-base.zip")
            with (data / "questions.csv").open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=["id", "question"])
                writer.writeheader()
                writer.writerows(questions)
            environment = os.environ.copy()
            environment.pop("PYTHONPATH", None)
            environment.update(
                DATA_DIR=str(data), MAX_WORKERS=str(workers), SUBMISSION_TIMEOUT="180"
            )
            started = time.monotonic()
            process = subprocess.Popen(
                [sys.executable, "solution.py"],
                cwd=work,
                env=environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            throttled = False
            events = []
            try:
                for line in process.stderr:
                    if "rate limited" in line.lower() or "ModelRateLimited" in line:
                        throttled = True
                        process.terminate()
                        break
                    try:
                        events.append(json.loads(line))
                    except ValueError:
                        pass
                process.wait(timeout=90)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
            result = {
                "workers": workers,
                "questions": len(questions),
                "elapsed_s": round(time.monotonic() - started, 3),
                "returncode": process.returncode,
                "throttled": throttled,
                "completed": sum(e.get("event") == "question_completed" for e in events),
                "events": events,
            }
            results.append(result)
            if (work / "submission.csv").exists():
                shutil.copyfile(work / "submission.csv", args.output / f"workers-{workers}.csv")
            (args.output / "metrics.json").write_text(
                json.dumps(results, indent=2), encoding="utf-8"
            )
            print(json.dumps({k: v for k, v in result.items() if k != "events"}), flush=True)
            if throttled:
                print("Stopped benchmark on rate limit; higher concurrency is not tested.")
                break


if __name__ == "__main__":
    main()
