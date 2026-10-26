"""Run the reviewed draft suite; all usage and observations stay temporary."""

import argparse
import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session

try:
    from .prepare_expert_review import cases
except ImportError:
    from prepare_expert_review import cases

from careerpilot.cli import provision
from careerpilot.deepseek_provider import SETTINGS_VERSION, from_env
from careerpilot.domain import Principal
from careerpilot.expert_comparison import compare, replay
from careerpilot.expert_protocol import CaseFile
from careerpilot.gateway import Gateway, strict_json
from careerpilot.persistence import User, make_engine
from careerpilot.platform_models import Consent
from careerpilot.safety import scan_upload


def load_cases(path):
    if path is None:
        return cases(), "synthetic_draft"
    with path.open("rb") as stream:
        raw = stream.read(1048577)
    if len(raw) > 1048576:
        raise ValueError("Case file exceeds limit")
    value = CaseFile.model_validate(strict_json(scan_upload(raw, "expert-cases.json")))
    return value.cases, (
        "synthetic_draft" if value.source == "synthetic" else "operator_supplied_unverified"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reviewed-sha256", required=True)
    parser.add_argument("--budget-micros-cny", required=True, type=int)
    parser.add_argument("--token-price-micros-cny", required=True, type=int)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--cases", type=Path, help="explicitly authorized reviewed case file")
    parser.add_argument(
        "--show-steps", action="store_true", help="sanitized observations on stdout"
    )
    parser.add_argument("--allow-external", action="store_true")
    args = parser.parse_args()
    if not args.allow_external:
        parser.exit(2, "Explicit external-call authorization is required.\n")
    try:
        with TemporaryDirectory(prefix="careerpilot-live-experts-") as directory:
            suite, source = load_cases(args.cases)
            url = "sqlite:///" + (Path(directory) / "usage.db").as_posix()
            config = Config("alembic.ini")
            config.attributes["database_url"] = url
            command.upgrade(config, "head")
            engine = make_engine(url)
            try:
                uid, _ = provision(engine, "Temporary expert comparison")
                with Session(engine) as db, db.begin():
                    user = db.get(User, uid)
                    principal = Principal(uid, user.tenant_id)
                    db.add(
                        Consent(
                            user_id=uid,
                            tenant_id=user.tenant_id,
                            purpose="external_model",
                            granted=1,
                        )
                    )
                # Supply a currently verified upper rate for every input/output token;
                # do not assume cache discounts or reuse a dated price silently.
                gateways = [
                    Gateway(
                        engine,
                        from_env(),
                        micros_per_token=args.token_price_micros_cny,
                        daily_tokens=1000000,
                        task_tokens=1000000,
                    )
                    for _ in range(2)
                ]
                result = asyncio.run(
                    compare(
                        suite,
                        args.reviewed_sha256,
                        principal,
                        *gateways,
                        repeats=args.repeats,
                        budget_micros=args.budget_micros_cny,
                        allow_external=True,
                        source=source,
                        progress=None
                        if args.show_steps
                        else lambda done, total: (
                            print(f"Handoffs: {done}/{total}", flush=True)
                            if done % 8 == 0
                            else None
                        ),
                    )
                )
                frozen = replay(suite, result)
                output = {
                    key: value for key, value in result.items() if key != "rows" or args.show_steps
                }
                output.update(
                    currency="CNY",
                    provider_settings=SETTINGS_VERSION,
                    token_price_micros=args.token_price_micros_cny,
                    frozen_replay_identical=frozen["identical"],
                )
                print(json.dumps(output, ensure_ascii=True, allow_nan=False))
                if result["stopped"] or not frozen["identical"]:
                    parser.exit(3, "Comparison incomplete; collaboration stays off.\n")
            finally:
                engine.dispose()
    except Exception:
        parser.exit(2, "Expert comparison could not run; no raw provider content printed.\n")


if __name__ == "__main__":
    main()
