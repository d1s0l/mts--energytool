"""Actual model -> HTTP routes -> PostgreSQL -> corrected lead, in a disposable schema."""

import argparse
import asyncio
import os
import secrets
import time
from pathlib import Path
from uuid import uuid4

from app.consultant import ConsultantReply
from consultant_impl import EnergyConsultant, GroundingReviewer, ModelConfig
from evaluate_answers import EvaluationClient, save
from evaluate_dialogs import SCENARIOS, check_turn


async def verify(kb, db, output, interval=0, turn_interval=45):
    import httpx
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    from psycopg.rows import dict_row
    from app.config import Settings
    from app.main import create_app

    schema = "verify_" + uuid4().hex
    report = {
        "status": "running",
        "actions_simulated": False,
        "scenarios": {},
        "schema_removed": False,
    }
    model = EvaluationClient(ModelConfig.from_env(), interval=interval, trace=True)
    scoped_db = make_conninfo(db, options=f"-c search_path={schema}", connect_timeout=5)
    access = secrets.token_urlsafe(24)
    last_turn_started = 0.0
    async with await psycopg.AsyncConnection.connect(
        db, autocommit=True, row_factory=dict_row
    ) as conn:
        await conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            app = create_app(
                Settings(database_url=scoped_db, knowledge_base_path=kb, api_access_token=access),
                consultant=EnergyConsultant(model, reviewer=GroundingReviewer(model)),
            )
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://verification",
                    headers={"Authorization": "Bearer " + access},
                ) as client:
                    for name, turns in SCENARIOS.items():
                        rows = report["scenarios"][name] = []
                        response = await client.post("/api/sessions")
                        response.raise_for_status()
                        sid = response.json()["id"]
                        saved_lead_id = None
                        for index, text in enumerate(turns):
                            start = len(model.traces)
                            row = {"index": index, "user": text, "status": "running"}
                            try:
                                if (name == "contacts_and_correction" and index == 6) or (
                                    name == "separate_objects" and index == 3
                                ):
                                    old = await client.get(f"/api/sessions/{sid}")
                                    assert old.status_code == 200 and old.json()["messages"], (
                                        "history_not_preserved"
                                    )
                                    response = await client.post("/api/sessions")
                                    response.raise_for_status()
                                    sid = response.json()["id"]
                                path = f"/api/sessions/{sid}"
                                request = {"text": text, "client_message_id": str(uuid4())}
                                await asyncio.sleep(
                                    max(0, turn_interval - (time.monotonic() - last_turn_started))
                                )
                                last_turn_started = time.monotonic()
                                for attempt in range(3):
                                    response = await client.post(path + "/messages", json=request)
                                    if response.status_code != 429 or attempt == 2:
                                        break
                                    delay = float(response.headers.get("Retry-After", "60"))
                                    if delay > 120:
                                        break
                                    row["rate_limit_retries"] = attempt + 1
                                    report["pending_turn"] = {
                                        "scenario": name,
                                        "index": index,
                                        "phase": "cooldown",
                                        "retry_after": delay,
                                    }
                                    save(output, report)
                                    print(
                                        f"{name}/{index + 1}: rate limited, waiting {delay:g}s",
                                        flush=True,
                                    )
                                    await asyncio.sleep(delay)
                                report.pop("pending_turn", None)
                                row["http_status"] = response.status_code
                                if response.status_code == 429:
                                    report["status"] = "rate_limited"
                                    row["status"] = "rate_limited"
                                    return report
                                response.raise_for_status()
                                result = response.json()
                                detail = (await client.get(path)).json()
                                lead_response = await client.get(path + "/lead")
                                lead = (
                                    lead_response.json()
                                    if lead_response.status_code == 200
                                    else None
                                )
                                reply = ConsultantReply(
                                    text=result["assistant_message"]["text"],
                                    state="chatting"
                                    if result["state"] == "lead_created"
                                    else result["state"],
                                    dialog_memory=detail["dialog_memory"],
                                    requested_action="create_lead"
                                    if result.get("lead_id")
                                    else "none",
                                    lead={k: lead[k] for k in ("full_name", "company", "phone")}
                                    if result.get("lead_id") and lead
                                    else None,
                                    lead_summary=lead["summary"]
                                    if result.get("lead_id") and lead
                                    else None,
                                )
                                row.update(answer=reply.text, memory=reply.dialog_memory, lead=lead)
                                check_turn(name, index, reply)
                                if name == "contacts_and_correction" and index in {4, 5}:
                                    assert lead and result["state"] == "lead_created", (
                                        "lead_not_persisted"
                                    )
                                    if saved_lead_id:
                                        assert lead["id"] == saved_lead_id, (
                                            "correction_created_duplicate_lead"
                                        )
                                    saved_lead_id = lead["id"]
                                    count = await conn.execute(
                                        sql.SQL(
                                            "SELECT count(*) AS n FROM {}.leads WHERE session_id=%s"
                                        ).format(sql.Identifier(schema)),
                                        (sid,),
                                    )
                                    assert (await count.fetchone())["n"] == 1, "duplicate_leads"
                                    calls_before = model.calls
                                    retry = await client.post(path + "/messages", json=request)
                                    assert retry.status_code == 200 and retry.json() == result, (
                                        "retry_not_idempotent"
                                    )
                                    assert model.calls == calls_before, "retry_called_model_again"
                                if name == "contacts_and_correction" and index <= 3:
                                    assert lead is None, "premature_lead"
                                row.update(
                                    status="passed",
                                    answer=reply.text,
                                    memory=reply.dialog_memory,
                                    lead=lead,
                                    business_checks="passed",
                                )
                            except Exception as exc:
                                row.update(status="failed", error_type=type(exc).__name__)
                                if isinstance(exc, AssertionError):
                                    row["check"] = str(exc)
                            finally:
                                row["request_traces"] = model.traces[start:]
                                rows.append(row)
                                save(output, report)
                                print(f"{name}/{index + 1}: {row['status']}", flush=True)
                            if row["status"] != "passed":
                                break
            report["status"] = (
                "complete"
                if all(
                    len(report["scenarios"].get(n, [])) == len(t)
                    and all(r["status"] == "passed" for r in report["scenarios"][n])
                    for n, t in SCENARIOS.items()
                )
                else "failed"
            )
        finally:
            await conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
            report["schema_removed"] = True
            save(output, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", type=Path, required=True)
    parser.add_argument("--kb", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--interval", type=float, default=0)
    parser.add_argument("--turn-interval", type=float, default=45)
    args = parser.parse_args()
    from dotenv import dotenv_values

    inherited_db = os.environ.get("TEST_DATABASE_URL")
    os.environ.update({k: v for k, v in dotenv_values(args.env).items() if v is not None})
    db = inherited_db or os.environ.get("TEST_DATABASE_URL")
    if not db:
        save(
            args.output,
            {
                "status": "unavailable",
                "reason": "TEST_DATABASE_URL_required",
                "actions_simulated": False,
            },
        )
        return 1
    try:
        report = asyncio.run(verify(args.kb, db, args.output, args.interval, args.turn_interval))
    except Exception as exc:
        report = {
            "status": "unavailable",
            "error_type": type(exc).__name__,
            "actions_simulated": False,
        }
        save(args.output, report)
    return 0 if report["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
