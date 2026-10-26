"""Disposable PostgreSQL migration/ownership contract. Never point at production."""

import os

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
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from study_contract import create_study, verify_study
from v4_contract import create_v4, verify_v4

from careerpilot.api import create_app
from careerpilot.cli import provision
from careerpilot.growth_models import GrowthRecord, GrowthRevision
from careerpilot.hiring_reminders import schedule_due as schedule_reminders
from careerpilot.persistence import CareerGoal, User, make_engine
from careerpilot.runtime import run_once
from careerpilot.seed import seed


def main():
    url = os.environ["CAREERPILOT_TEST_POSTGRES_URL"]
    if "careerpilot_test" not in url:
        raise RuntimeError("This drill requires an explicitly disposable test database")
    config = Config("alembic.ini")
    config.attributes["database_url"] = url
    engine = make_engine(url)
    try:
        command.upgrade(config, "head")
        command.check(config)
        alice, alice_token = provision(engine, "PG synthetic Alice")
        bob, bob_token = provision(engine, "PG synthetic Bob")
        with Session(engine) as db:
            alice_tenant, bob_tenant = db.get(User, alice).tenant_id, db.get(User, bob).tenant_id
        try:
            with Session(engine) as db, db.begin():
                db.add(CareerGoal(user_id=alice, tenant_id=bob_tenant, title="bad FK"))
        except IntegrityError:
            pass
        else:
            raise AssertionError("Composite ownership FK not enforced")
        try:
            with Session(engine) as db, db.begin():
                db.add_all(
                    [
                        CareerGoal(
                            user_id=alice, tenant_id=alice_tenant, title="active", status="active"
                        )
                        for _ in range(2)
                    ]
                )
        except IntegrityError:
            pass
        else:
            raise AssertionError("Single active goal constraint not enforced")
        with Session(engine) as db, db.begin():
            record = GrowthRecord(
                user_id=alice, tenant_id=alice_tenant, kind="memory", payload_json="{}"
            )
            db.add(record)
            db.flush()
            record_id = record.id
        for invalid in [
            GrowthRecord(user_id=alice, tenant_id=bob_tenant, kind="memory", payload_json="{}"),
            GrowthRevision(
                record_id=record_id, version=1, user_id=bob, tenant_id=bob_tenant, payload_json="{}"
            ),
        ]:
            try:
                with Session(engine) as db, db.begin():
                    db.add(invalid)
            except IntegrityError:
                pass
            else:
                raise AssertionError("V1 aggregate/revision composite ownership not enforced")
        with Session(engine) as db, db.begin():
            db.delete(db.get(GrowthRecord, record_id))
        seed(engine)
        with TestClient(create_app(url)) as client:
            headers = {"Authorization": "Bearer " + alice_token}
            profile = client.put(
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
            )
            assert profile.status_code == 200, profile.status_code
            job = client.post(
                "/api/v1/jobs/import",
                headers=headers,
                json={"content": "岗位：工程师\n要求：Python"},
            )
            assert job.status_code == 201, job.status_code
            plan = client.post(
                "/api/v1/plans",
                headers=headers,
                json={"job_ids": [job.json()["id"]], "start_date": "2026-10-20"},
            )
            assert plan.status_code == 201, plan.status_code
            published = client.post(
                f"/api/v1/plans/{plan.json()['id']}/publish",
                headers={**headers, "If-Match": '"1"'},
                json={"confirmed": True},
            )
            assert published.status_code == 200, published.status_code
            exam = client.post("/api/v1/assessments", headers=headers, json={"skill_id": "python"})
            assert exam.status_code == 201, exam.status_code
            scored = client.post(
                f"/api/v1/assessments/{exam.json()['id']}/submit",
                headers={**headers, "If-Match": '"1"'},
                json={"answers": [0, 0, 0]},
            )
            assert scored.status_code == 200, scored.status_code
            assert scored.json()["status"] == "failed" and scored.json()["replan_preview"]
            limits = client.put(
                "/api/v1/matching-constraints",
                headers={**headers, "If-Match": '"0"'},
                json={"available_months": 4, "confirmed": True},
            )
            assert limits.status_code == 200, limits.status_code
            stale = client.put(
                "/api/v1/matching-constraints",
                headers={**headers, "If-Match": '"0"'},
                json={"available_months": 5, "confirmed": True},
            )
            assert stale.status_code == 412, stale.status_code
            matches = client.get("/api/v1/job-matches", headers=headers)
            assert matches.status_code == 200 and not matches.json()["eligible"]
            assert len(matches.json()["needs_review"]) == 1
            snapshot = client.post(
                "/api/v1/job-matches/recompute",
                headers=headers,
                json={"job_ids": [job.json()["id"]]},
            )
            assert snapshot.status_code == 201, snapshot.status_code
            path = f"/api/v1/job-match-snapshots/{snapshot.json()['id']}"
            assert client.get(path, headers=headers).json() == snapshot.json()
            assert (
                client.get(path, headers={"Authorization": "Bearer " + bob_token}).status_code
                == 404
            )
            comparison = client.get("/api/v1/career-comparison", headers=headers)
            assert comparison.status_code == 200 and len(comparison.json()["careers"]) == 4
            preferred = client.put(
                "/api/v1/job-preferences",
                headers={**headers, "If-Match": '"0"'},
                json={"favorite_ids": [job.json()["id"]], "confirmed": True},
            )
            assert preferred.status_code == 200
            feedback = client.put(
                f"/api/v1/jobs/{job.json()['id']}/feedback",
                headers={**headers, "If-Match": '"0"'},
                json={"label": "like", "confirmed": True},
            )
            assert feedback.status_code == 200
            recommended = client.get("/api/v1/job-recommendations", headers=headers)
            assert recommended.json()["needs_review"][0]["feedback_adjustment"] == 5
            subscription = client.post(
                "/api/v1/job-subscriptions",
                headers={**headers, "Idempotency-Key": "pg-subscribe"},
                json={
                    "name": "Synthetic PG subscription",
                    "quiet_start": 0,
                    "quiet_end": 0,
                    "confirmed": True,
                },
            )
            assert subscription.status_code == 201, subscription.status_code
            sub_id = subscription.json()["id"]
            for key in ("pg-first", "pg-repeat"):
                queued = client.post(
                    f"/api/v1/job-subscriptions/{sub_id}/check",
                    headers={**headers, "Idempotency-Key": key},
                    json={"confirmed": True},
                )
                assert queued.status_code == 202, queued.status_code
                assert run_once(engine)
                assert len(client.get("/api/v1/job-notifications", headers=headers).json()) == 1
            assert (
                client.get(
                    f"/api/v1/job-subscriptions/{sub_id}/runs",
                    headers={"Authorization": "Bearer " + bob_token},
                ).status_code
                == 404
            )
            undone = client.post(
                f"/api/v1/jobs/{job.json()['id']}/feedback/undo",
                headers={**headers, "If-Match": '"1"'},
                json={"confirmed": True},
            )
            assert undone.status_code == 200 and not undone.json()["active"]
            hiring = create_hiring(client, headers, job.json()["id"], "pg-hiring")
            verify_hiring(client, headers, hiring)
            practice = create_practice(
                client,
                headers,
                job.json()["id"],
                hiring["version"]["id"],
                plan.json()["id"],
                "pg-practice",
            )
            verify_practice(client, headers, practice)
            verify_sources(client, headers, create_sources(client, headers))
            verify_progress(
                client,
                headers,
                engine,
                create_progress(client, headers, plan.json()["id"], "pg-progress"),
            )
            verify_public_maintenance(engine, create_public_maintenance(client, engine))
            verify_project_workflow(client, engine, create_project_workflow(client, engine))
            verify_study(client, create_study(client, engine))
            verify_history(client, create_history(client, engine))
            verify_v4(client, engine, create_v4(client, engine))
            space = client.post(
                "/api/v1/workspaces",
                headers=headers,
                json={"name": "PG V4 workspace", "confirmed": True},
            )
            assert space.status_code == 201
            assert (
                client.get(
                    "/api/v1/object-shares",
                    headers={"Authorization": "Bearer " + bob_token},
                    params={"workspace_id": space.json()["id"]},
                ).status_code
                == 404
            )
            direction = client.post(
                "/api/v1/direction-views",
                headers=headers,
                json={
                    "title": "PG V4 direction",
                    "fact_ids": [],
                    "project_ids": [],
                    "confirmed": True,
                },
            )
            assert direction.status_code == 201
            assert (
                client.get(
                    f"/api/v1/direction-views/{direction.json()['id']}",
                    headers={"Authorization": "Bearer " + bob_token},
                ).status_code
                == 404
            )
            assert (
                client.get(
                    f"/api/v1/resume-versions/{hiring['version']['id']}",
                    headers={"Authorization": "Bearer " + bob_token},
                ).status_code
                == 404
            )
            assert schedule_reminders(engine, hiring["reminder"]["due_at"] + 1) == 1
            assert schedule_reminders(engine, hiring["reminder"]["due_at"] + 2) == 0
        command.downgrade(config, "base")
        assert inspect(engine).get_table_names() == ["alembic_version"]
        command.upgrade(config, "head")
        command.check(config)
        print("PostgreSQL migration roundtrip, ownership FK and active-goal uniqueness passed")
        print("V3 PostgreSQL study mode acceptance, private feedback and review history passed")
        print("V1 growth aggregate and revision composite ownership FK passed")
        print("V1 PostgreSQL profile, JD, budget plan, objective assessment and replan passed")
        print(
            "V2 PostgreSQL constraints CAS, three-value matching, "
            "private snapshot and careers passed"
        )
        print("V2 PostgreSQL preferences, feedback undo, subscription Worker and dedup passed")
        print(
            "V2 PostgreSQL immutable resume binding, application history and reminder dedup passed"
        )
        print("V2 PostgreSQL interview, editable review and confirmed plan preview passed")
        print("V2 PostgreSQL private source import, conflict quotes and physical deletion passed")
        print(
            "V2 PostgreSQL explicit study time, report, pipeline and scheduled notice dedup passed"
        )
        print("V2 PostgreSQL independently reviewed public publish and rollback passed")
        print(
            "V3 PostgreSQL project milestones/assets/STAR, pending evaluation "
            "and plan acceptance passed (simulated verdict)"
        )
        print("V4 PostgreSQL workspace/direction isolation and schema 0005 roundtrip passed")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
