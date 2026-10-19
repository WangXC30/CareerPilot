"""Local-only identity provisioning; raw credentials are never persisted."""

import argparse
import hashlib
import secrets
import time

from sqlalchemy.orm import Session

from careerpilot.persistence import SessionToken, User, database_url, make_engine, new_id


def provision(engine, name: str, ttl: int = 86400) -> tuple[str, str]:
    if not name.strip() or ttl <= 0:
        raise ValueError("Name and positive token lifetime required")
    token = secrets.token_urlsafe(32)
    user_id, tenant_id = new_id(), new_id()
    with Session(engine) as session, session.begin():
        session.add(User(id=user_id, tenant_id=tenant_id, display_name=name))
        session.flush()
        session.add(
            SessionToken(
                digest=hashlib.sha256(token.encode()).hexdigest(),
                user_id=user_id,
                tenant_id=tenant_id,
                expires_at=int(time.time()) + ttl,
            )
        )
    return user_id, token


def main():
    parser = argparse.ArgumentParser(description="Provision a local development user")
    parser.add_argument("--name", required=True)
    args = parser.parse_args()
    engine = make_engine(database_url())
    try:
        user_id, token = provision(engine, args.name)
        print(f"User: {user_id}\nBearer token (shown once, valid 24h): {token}")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
