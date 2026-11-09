"""Start a real loopback server against a disposable migrated database."""

import socket
import tempfile
import threading
import time
from pathlib import Path

import httpx
import uvicorn
from alembic import command
from alembic.config import Config

from careerpilot.api import create_app
from careerpilot.cli import provision
from careerpilot.persistence import make_engine


def main():
    with tempfile.TemporaryDirectory(prefix="careerpilot-smoke-") as directory:
        url = f"sqlite:///{(Path(directory) / 'smoke.db').as_posix()}"
        config = Config("alembic.ini")
        config.attributes["database_url"] = url
        command.upgrade(config, "head")
        engine = make_engine(url)
        _, token = provision(engine, "smoke")
        engine.dispose()
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(
                create_app(url, local_only=False), host="127.0.0.1", port=port, log_level="error"
            )
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            with httpx.Client(
                base_url=f"http://127.0.0.1:{port}", timeout=2, trust_env=False
            ) as client:
                for _ in range(50):
                    if not thread.is_alive():
                        raise RuntimeError("Server exited during startup")
                    try:
                        if client.get("/health/ready").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.1)
                else:
                    raise RuntimeError("Server did not become ready")
                headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": "smoke"}
                created = client.post(
                    "/api/v1/career-goals", headers=headers, json={"title": "后端开发"}
                )
                assert created.status_code == 201, created.status_code
                assert (
                    client.get("/api/v1/career-goals", headers=headers).json()[0] == created.json()
                )
                assert client.get("/api/v1/me").status_code == 401
                print("HTTP smoke passed: readiness, authenticated create/list, anonymous denial")
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            if thread.is_alive():
                raise RuntimeError("Server failed to stop")


if __name__ == "__main__":
    main()
