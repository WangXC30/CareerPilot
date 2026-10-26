"""Reproducible local API p95 and backup/restore integrity drill; synthetic users only."""

import argparse
import json
import sqlite3
import tempfile
import time
from contextlib import closing
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from hiring_contract import create_hiring, verify_hiring
from interview_contract import create_practice, verify_practice
from maintenance_contract import create_public_maintenance, verify_public_maintenance
from profile_history_contract import create_history, verify_history
from progress_contract import create_progress, verify_progress
from project_workflow_contract import create_project_workflow, verify_project_workflow
from source_contract import create_sources, verify_sources
from study_contract import create_study, verify_study
from v4_contract import create_v4, verify_v4

from careerpilot.api import create_app
from careerpilot.backup import snapshot
from careerpilot.cli import provision
from careerpilot.hiring_reminders import schedule_due as schedule_reminders
from careerpilot.persistence import make_engine
from careerpilot.runtime import run_once
from careerpilot.seed import seed

try:
    from .temporary_artifacts import temporary_artifacts
except ImportError:
    from temporary_artifacts import temporary_artifacts


def drill():
    with tempfile.TemporaryDirectory(prefix="careerpilot-drill-") as directory:
        source, backup, restored = [
            Path(directory) / p for p in ["source.db", "backup.db", "restored.db"]
        ]
        url = f"sqlite:///{source.as_posix()}"
        config = Config("alembic.ini")
        config.attributes["database_url"] = url
        command.upgrade(config, "head")
        engine = make_engine(url)
        seed(engine)
        _, token = provision(engine, "synthetic-drill")
        engine.dispose()
        headers = {"Authorization": "Bearer " + token, "Idempotency-Key": "drill-goal"}
        times = []
        with TestClient(create_app(url)) as client:
            assert (
                client.post(
                    "/api/v1/career-goals", headers=headers, json={"title": "Synthetic goal"}
                ).status_code
                == 201
            )
            assert (
                client.put(
                    "/api/v1/profile",
                    headers={**headers, "If-Match": '"0"'},
                    json={
                        "education": "bachelor",
                        "major": "CS",
                        "academic_year": "3",
                        "graduation_date": "2027-06-30",
                        "preferred_cities": ["上海"],
                        "weekly_minutes": 600,
                        "confirmed": True,
                    },
                ).status_code
                == 200
            )
            job = client.post(
                "/api/v1/jobs/import",
                headers=headers,
                json={"content": "岗位：工程师\n要求：Python"},
            )
            assert job.status_code == 201
            plan = client.post(
                "/api/v1/plans",
                headers=headers,
                json={"job_ids": [job.json()["id"]], "start_date": "2026-10-20"},
            )
            assert plan.status_code == 201
            plan_id = plan.json()["id"]
            assert (
                client.post(
                    f"/api/v1/plans/{plan_id}/publish",
                    headers={**headers, "If-Match": '"1"'},
                    json={"confirmed": True},
                ).status_code
                == 200
            )
            exam = client.post("/api/v1/assessments", headers=headers, json={"skill_id": "python"})
            assert exam.status_code == 201
            scored = client.post(
                f"/api/v1/assessments/{exam.json()['id']}/submit",
                headers={**headers, "If-Match": '"1"'},
                json={"answers": [0, 0, 0]},
            )
            assert scored.status_code == 200
            evidence_id = scored.json()["evidence"]["id"]
            assert (
                client.put(
                    "/api/v1/job-preferences",
                    headers={**headers, "If-Match": '"0"'},
                    json={"favorite_ids": [job.json()["id"]], "confirmed": True},
                ).status_code
                == 200
            )
            assert (
                client.put(
                    f"/api/v1/jobs/{job.json()['id']}/feedback",
                    headers={**headers, "If-Match": '"0"'},
                    json={"label": "like", "confirmed": True},
                ).status_code
                == 200
            )
            explanation = client.post(
                "/api/v1/job-matches/recompute",
                headers=headers,
                json={"job_ids": [job.json()["id"]]},
            ).json()
            subscription = client.post(
                "/api/v1/job-subscriptions",
                headers={**headers, "Idempotency-Key": "drill-sub-create"},
                json={
                    "name": "Synthetic library subscription",
                    "quiet_start": 0,
                    "quiet_end": 0,
                    "confirmed": True,
                },
            )
            assert subscription.status_code == 201
            sub_id = subscription.json()["id"]
            assert (
                client.post(
                    f"/api/v1/job-subscriptions/{sub_id}/check",
                    headers={**headers, "Idempotency-Key": "drill-sub-run-1"},
                    json={"confirmed": True},
                ).status_code
                == 202
            )
            assert run_once(engine)
            assert len(client.get("/api/v1/job-notifications", headers=headers).json()) == 1
            pending = client.post(
                f"/api/v1/job-subscriptions/{sub_id}/check",
                headers={**headers, "Idempotency-Key": "drill-sub-run-2"},
                json={"confirmed": True},
            )
            assert pending.status_code == 202
            hiring = create_hiring(client, headers, job.json()["id"], "restore-hiring")
            sources = create_sources(client, headers)
            progress = create_progress(client, headers, plan_id, "restore-progress")
            public_maintenance = create_public_maintenance(client, engine)
            practice = create_practice(
                client,
                headers,
                job.json()["id"],
                hiring["version"]["id"],
                plan_id,
                "restore-practice",
            )
            project_workflow = create_project_workflow(client, engine)
            study_state = create_study(client, engine)
            history_state = create_history(client, engine)
            v4_state = create_v4(client, engine)
            v4_space = client.post(
                "/api/v1/workspaces",
                headers=headers,
                json={"name": "恢复演练空间", "confirmed": True},
            )
            assert v4_space.status_code == 201
            v4_space_id = v4_space.json()["id"]
            v4_direction = client.post(
                "/api/v1/direction-views",
                headers=headers,
                json={
                    "title": "恢复演练方向",
                    "fact_ids": [],
                    "project_ids": [],
                    "confirmed": True,
                },
            )
            assert v4_direction.status_code == 201
            v4_direction_id = v4_direction.json()["id"]
            for _ in range(100):
                start = time.perf_counter()
                assert client.get("/api/v1/career-goals", headers=headers).status_code == 200
                times.append((time.perf_counter() - start) * 1000)
        start = time.perf_counter()
        snapshot(source, backup)
        snapshot(backup, restored)
        recovery_ms = (time.perf_counter() - start) * 1000
        with closing(sqlite3.connect(restored)) as db:
            assert db.execute("SELECT title FROM career_goals").fetchone()[0] == "Synthetic goal"
            assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        recovered_url = f"sqlite:///{restored.as_posix()}"
        recovered_engine = make_engine(recovered_url)
        with TestClient(create_app(recovered_url)) as client:
            assert client.get("/health/ready").status_code == 200
            verify_v4(client, recovered_engine, v4_state)
            assert any(
                space["id"] == v4_space_id
                for space in client.get("/api/v1/workspaces", headers=headers).json()["spaces"]
            )
            assert (
                client.get(f"/api/v1/direction-views/{v4_direction_id}", headers=headers).json()[
                    "title"
                ]
                == "恢复演练方向"
            )
            assert (
                client.get("/api/v1/career-goals", headers=headers).json()[0]["title"]
                == "Synthetic goal"
            )
            assert (
                client.get(f"/api/v1/plans/{plan_id}", headers=headers).json()["status"] == "active"
            )
            assert any(
                e["id"] == evidence_id
                for e in client.get("/api/v1/skill-evidence", headers=headers).json()
            )
            assert client.get("/api/v1/plan-previews", headers=headers).json()
            assert (
                client.get(
                    f"/api/v1/job-match-snapshots/{explanation['id']}", headers=headers
                ).json()
                == explanation
            )
            assert client.get("/api/v1/job-preferences", headers=headers).json()[
                "favorite_ids"
            ] == [job.json()["id"]]
            assert client.get("/api/v1/job-feedback", headers=headers).json()[0]["label"] == "like"
            verify_hiring(client, headers, hiring)
            # Verify and disable the separate study account's reminder before the
            # progress contract advances the global scheduler to its future clock.
            verify_study(client, study_state)
            verify_history(client, history_state)
            verify_progress(client, headers, recovered_engine, progress)
            verify_public_maintenance(recovered_engine, public_maintenance)
            verify_practice(client, headers, practice)
            verify_sources(client, headers, sources)
            verify_project_workflow(client, recovered_engine, project_workflow)
            assert schedule_reminders(recovered_engine, hiring["reminder"]["due_at"] + 1) == 1
            assert schedule_reminders(recovered_engine, hiring["reminder"]["due_at"] + 2) == 0
            assert len(client.get("/api/v1/application-notifications", headers=headers).json()) == 1
            run_once(recovered_engine)
            assert len(client.get("/api/v1/job-notifications", headers=headers).json()) == 1
            runs = client.get(f"/api/v1/job-subscriptions/{sub_id}/runs", headers=headers).json()
            assert (
                next(r for r in runs if r["id"] == pending.json()["id"])["task"]["status"]
                == "success"
            )
        recovered_engine.dispose()
        engine.dispose()
        return {
            "mode": "local_TestClient_synthetic",
            "requests": 100,
            "p50_ms": round(sorted(times)[49], 3),
            "p95_ms": round(sorted(times)[94], 3),
            "backup_restore_ms": round(recovery_ms, 3),
            "restored_integrity": "ok",
            "restored_auth_and_data": True,
            "restored_growth_data": True,
            "restored_match_snapshot": True,
            "restored_discovery_data": True,
            "restored_resume_version_application_history_and_pending_reminder": True,
            "restored_pending_subscription_without_duplicate_notification": True,
            "restored_interview_review_and_pending_preview_accepted_once": True,
            "restored_private_sources_conflict_quotes_and_deletion": True,
            "restored_pending_review_pipeline_study_logs_and_report_notice": True,
            "restored_public_catalogue_versions_and_reviewed_rollback": True,
            "restored_v3_project_assets_star_pending_evaluation_and_plan_preview": True,
            "restored_v3_study_mode_feedback_review_history_and_reminder_opt_out": True,
            "restored_v4_workspace_and_direction": True,
            "restored_v4_pending_invitation_permanent_revocation_audio_purge_and_trends": True,
            "RPO": "snapshot boundary; no post-snapshot writes in drill",
            "production_slo_measured": False,
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    with temporary_artifacts(args.output, filename=True) as args.output:
        result = drill()
        if args.output:
            args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result))
