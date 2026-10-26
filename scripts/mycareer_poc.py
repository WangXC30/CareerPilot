"""Executed only in the isolated audited reference environment. No code vendored."""

import tempfile
from pathlib import Path

from career_agent.storage.api_keys import WORKSPACE_READ, SQLiteApiKeyStore


def main():
    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteApiKeyStore(Path(directory) / "keys.db")
        issued = store.issue(
            user_id="legacy-owner", name="synthetic-poc", scopes=frozenset({WORKSPACE_READ})
        )
        mappings = {"legacy-owner": ("tenant-poc", "user-poc")}
        principal = store.verify(issued.secret)
        assert principal and principal.allows(WORKSPACE_READ)
        trusted_identity = mappings.get(principal.user_id)
        assert trusted_identity == ("tenant-poc", "user-poc")
        assert mappings.get("unmapped-user") is None
        store.revoke(key_id=issued.key_id)
        assert store.verify(issued.secret) is None
        print(
            "MyCareer identity adapter PoC passed: trusted mapping, scope, revocation; "
            "no credentials emitted"
        )


if __name__ == "__main__":
    main()
