"""Run configured Provider adapters with a disposable database; print no raw model content."""

import argparse
import asyncio
import importlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session

from careerpilot.cli import provision
from careerpilot.domain import Principal
from careerpilot.gateway import Gateway, strict_json
from careerpilot.model_comparison import Dataset, compare, replay
from careerpilot.persistence import User, make_engine
from careerpilot.platform_models import Consent
from careerpilot.safety import scan_upload


def provider(spec):
    module, separator, factory = spec.partition(":")
    if not separator or not module or not factory.isidentifier():
        raise ValueError("adapter must be a locally configured module:factory")
    return getattr(importlib.import_module(module), factory)()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--primary", required=True, help="trusted local module:factory")
    parser.add_argument("--reviewer", required=True, help="trusted local module:factory")
    parser.add_argument("--primary-price-micros", type=int, required=True)
    parser.add_argument("--reviewer-price-micros", type=int, required=True)
    parser.add_argument("--budget-micros", type=int, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--allow-external", action="store_true")
    parser.add_argument(
        "--show-steps", action="store_true", help="include sanitized observations on stdout"
    )
    args = parser.parse_args()
    try:
        with args.dataset.open("rb") as stream:
            raw = stream.read(1024 * 1024 + 1)
        dataset = Dataset.model_validate(strict_json(scan_upload(raw, "dataset.json")))
        primary, reviewer = provider(args.primary), provider(args.reviewer)
        with TemporaryDirectory(prefix="careerpilot-model-comparison-") as scratch:
            url = "sqlite:///" + (Path(scratch) / "usage.db").as_posix()
            config = Config("alembic.ini")
            config.attributes["database_url"] = url
            command.upgrade(config, "head")
            engine = make_engine(url)
            try:
                uid, _ = provision(engine, "Temporary model comparison")
                with Session(engine) as db, db.begin():
                    user = db.get(User, uid)
                    principal = Principal(uid, user.tenant_id)
                    if args.allow_external:
                        db.add(
                            Consent(
                                user_id=uid,
                                tenant_id=user.tenant_id,
                                purpose="external_model",
                                granted=1,
                            )
                        )
                gateways = [
                    Gateway(
                        engine,
                        implementation,
                        micros_per_token=price,
                        daily_tokens=1_000_000,
                        task_tokens=1_000_000,
                    )
                    for implementation, price in [
                        (primary, args.primary_price_micros),
                        (reviewer, args.reviewer_price_micros),
                    ]
                ]
                result = asyncio.run(
                    compare(
                        dataset,
                        principal,
                        *gateways,
                        repeats=args.repeats,
                        budget_micros=args.budget_micros,
                        allow_external=args.allow_external,
                    )
                )
                frozen = replay(dataset, result)
                # Detailed predictions/steps live only in this temporary context.
                summary = {
                    key: value for key, value in result.items() if key != "rows" or args.show_steps
                }
                summary["frozen_replay_identical"] = frozen["identical"]
                print(json.dumps(summary, ensure_ascii=False, allow_nan=False))
                if result["stopped"] or not frozen["identical"]:
                    parser.exit(3, "Comparison incomplete; collaboration remains disabled.\n")
            finally:
                engine.dispose()
    except Exception:
        # Do not print exception text: adapters or datasets may contain private material.
        parser.exit(2, "Comparison could not run. Check the local dataset, adapter and limits.\n")


if __name__ == "__main__":
    main()
