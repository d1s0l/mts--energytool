"""One release gate: offline regressions, PostgreSQL/API, live persisted lead, actual flat ZIP."""

import argparse
import fcntl
import hashlib
import importlib.metadata
import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--env", type=Path, required=True)
    p.add_argument("--kb", type=Path, required=True)
    p.add_argument("--questions", type=Path, required=True)
    p.add_argument("--output-root", type=Path, required=True)
    p.add_argument("--approved-requirements", type=Path)
    p.add_argument("--offline", action="store_true")
    p.add_argument("--submission-python", type=Path)
    args = p.parse_args()
    args.output_root.mkdir(parents=True, exist_ok=True)
    gate_lock = (args.output_root / ".release-check.lock").open("a+")
    try:
        fcntl.flock(gate_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(
            "Another release verification is already running; no model requests started.",
            file=sys.stderr,
        )
        return 2
    from dotenv import dotenv_values

    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env.update({k: v for k, v in dotenv_values(args.env).items() if v is not None})
    if os.environ.get("TEST_DATABASE_URL"):
        env["TEST_DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
    env["PYTHONPATH"] = os.pathsep.join(
        str(root / n) for n in ("backend", "backend/tests", "rag", ".")
    )
    env["PYTHONUNBUFFERED"] = "1"
    digest = hashlib.sha256()
    for file in sorted([*(root / "backend").rglob("*.py"), *(root / "rag").glob("*.py")]):
        digest.update(str(file.relative_to(root)).encode())
        digest.update(file.read_bytes())
    for file in (args.kb, args.questions, root / "backend/requirements-submission.txt"):
        digest.update(file.read_bytes())
    for k in (
        "LLM_MODEL",
        "OPENAI_MODEL",
        "GEN_MODEL",
        "MAX_TOKENS",
        "TEMPERATURE",
        "RAG_MODE",
        "LLM_TIMEOUT",
        "LLM_REASONING_EFFORT",
        "LLM_MIN_INTERVAL",
        "SUBMISSION_MIN_INTERVAL",
        "EMB_MODEL",
        "RERANKER_MODEL",
    ):
        digest.update((k + "=" + env.get(k, "")).encode())
    commit = subprocess.check_output(
        ["git", "rev-parse", "--short", "HEAD"], cwd=root, text=True
    ).strip()
    output = args.output_root / f"release-{commit}-{digest.hexdigest()[:10]}"
    output.mkdir(parents=True, exist_ok=True)
    archive = output / "submission.zip"
    manifest = {
        "status": "running",
        "commit": commit,
        "source_fingerprint": digest.hexdigest(),
        "offline_only": args.offline,
        "stages": [],
        "artifact": str(archive),
        "python": sys.version.split()[0],
        "runtime_packages": {
            name: importlib.metadata.version(name) for name in ("pydantic", "httpx")
        },
    }
    secrets = [
        v
        for k, v in env.items()
        if v and any(s in k.upper() for s in ("API_KEY", "TOKEN", "PASSWORD"))
    ]
    for k in ("TEST_DATABASE_URL", "DATABASE_URL"):
        if env.get(k):
            secrets.append(env[k])
            password = urlsplit(env[k]).password
            if password:
                secrets.extend([password, unquote(password)])

    def redact(text):
        for value in sorted(set(secrets), key=len, reverse=True):
            if len(value) >= 4:
                text = text.replace(value, "[REDACTED]")
        return text

    def save():
        tmp = output / "manifest.tmp"
        tmp.write_text(redact(json.dumps(manifest, ensure_ascii=False, indent=2)))
        tmp.replace(output / "manifest.json")

    def skip(name, reason):
        manifest["stages"].append({"name": name, "status": "unavailable", "reason": reason})
        save()

    def stage(name, command, timeout=120, cwd=root, offline=False):
        print(f"{name}: running", flush=True)
        stage_env = env.copy()
        if offline:
            for k in (
                "LLM_MODEL",
                "OPENAI_MODEL",
                "GEN_MODEL",
                "LLM_BASE_URL",
                "OPENAI_BASE_URL",
                "LLM_API_KEY",
                "OPENAI_API_KEY",
            ):
                stage_env.pop(k, None)
        try:
            result = subprocess.run(
                command,
                cwd=cwd,
                env=stage_env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=timeout,
            )
            code = result.returncode
            log = result.stdout
        except subprocess.TimeoutExpired as exc:
            code = 124
            log = "Stage timeout. No success is claimed.\n"
            if exc.stdout:
                log += (
                    exc.stdout.decode(errors="replace")
                    if isinstance(exc.stdout, bytes)
                    else exc.stdout
                )
        (output / (name + ".log")).write_text(redact(log))
        manifest["stages"].append(
            {"name": name, "status": "passed" if code == 0 else "failed", "returncode": code}
        )
        save()
        print(f"{name}: {'passed' if code == 0 else 'failed'}", flush=True)
        return code

    save()
    try:
        unit = "import unittest; l=unittest.TestLoader(); s=unittest.TestSuite(l.loadTestsFromName(n) for n in ('test_dialog_memory','test_consultant_core','test_evaluation_limits','test_diagnostic_runner','test_release_verification')); r=unittest.TextTestRunner(verbosity=2).run(s); raise SystemExit(not r.wasSuccessful())"
        if stage("offline-core", [sys.executable, "-c", unit], offline=True):
            return 1
        if stage(
            "offline-rag",
            [sys.executable, "-m", "unittest", "discover", "-s", "rag/tests", "-v"],
            offline=True,
        ):
            return 1
        audit = [
            sys.executable,
            "backend/verify_submission.py",
            "--kb",
            str(args.kb),
            "--questions",
            str(args.questions),
            "--archive",
            str(archive),
            "--output",
            str(output / "archive-audit.json"),
            "--audit-only",
        ]
        if args.approved_requirements:
            audit += ["--approved-requirements", str(args.approved_requirements)]
        if stage("archive-audit", audit, offline=True):
            return 1
        if args.offline:
            manifest["status"] = "offline_complete"
            save()
            print(
                f"Offline checks passed; live model and PostgreSQL have NOT run. {output}",
                flush=True,
            )
            return 0
        db = env.get("TEST_DATABASE_URL")
        if not db:
            stage(
                "test-postgres-start",
                [
                    "docker",
                    "compose",
                    "-p",
                    "energytool-diagnostics",
                    "-f",
                    "compose.test.yaml",
                    "up",
                    "-d",
                    "--wait",
                    "postgres",
                ],
                timeout=90,
            )
            db = f"postgresql://energytool_test:energytool-test@127.0.0.1:{env.get('TEST_POSTGRES_PORT', '55432')}/energytool_test"
            env["TEST_DATABASE_URL"] = db
        db_ready = False
        if db:
            try:
                import psycopg
                from psycopg.conninfo import make_conninfo

                with psycopg.connect(make_conninfo(db, connect_timeout=5)):
                    pass
                db_ready = True
            except psycopg.Error:
                pass
        if db_ready:
            stage(
                "postgres-api",
                [sys.executable, "-m", "pytest", "-q", "--tb=short"],
                cwd=root / "backend",
                offline=True,
            )
        else:
            skip("postgres-api", "test_database_unavailable")
        try:
            url = env.get("LLM_BASE_URL") or env.get("OPENAI_BASE_URL") or ""
            socket.getaddrinfo(urlsplit(url).hostname, 443)
            provider_ready = True
        except (OSError, ValueError):
            provider_ready = False
        if provider_ready and db_ready:
            stage(
                "live-api-lead",
                [
                    sys.executable,
                    "backend/verify_live_api.py",
                    "--env",
                    str(args.env),
                    "--kb",
                    str(args.kb),
                    "--output",
                    str(output / "live-api.json"),
                ],
                timeout=1200,
            )
            api_path = output / "live-api.json"
            api_report = (
                json.loads(api_path.read_text()) if api_path.exists() else {"status": "failed"}
            )
            if api_report.get("status") == "rate_limited":
                skip("packaged-model", "provider_rate_limited")
                return 1
            stage(
                "provider-cooldown",
                [sys.executable, "-c", "import time; time.sleep(60)"],
                timeout=75,
            )
        else:
            skip("live-api-lead", "model_or_test_database_unavailable")
        if provider_ready:
            packaged = [
                sys.executable,
                "backend/verify_submission.py",
                "--env",
                str(args.env),
                "--kb",
                str(args.kb),
                "--questions",
                str(args.questions),
                "--archive",
                str(archive),
                "--output",
                str(output / "packaged-model.json"),
            ]
            if args.approved_requirements:
                packaged += ["--approved-requirements", str(args.approved_requirements)]
            if args.submission_python:
                packaged += ["--python", str(args.submission_python)]
            stage("packaged-model", packaged, timeout=1190)
        else:
            skip("packaged-model", "model_dns_unavailable")
    except KeyboardInterrupt:
        manifest["status"] = "interrupted"
        return 130
    finally:
        if manifest["status"] == "running":
            required = {
                "offline-core",
                "offline-rag",
                "archive-audit",
                "postgres-api",
                "live-api-lead",
                "packaged-model",
            }
            passed = {s["name"] for s in manifest["stages"] if s["status"] == "passed"}
            manifest["status"] = "complete" if required <= passed else "partial"
        save()
        print(f"Results: {output}", flush=True)
    return 0 if manifest["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
