"""Six domain boundaries and a scoped anti-corruption adapter contract."""

from dataclasses import dataclass
from typing import Any, Protocol

from careerpilot.domain import Principal
from careerpilot.states import authorize


class ProfilePort(Protocol):
    def read(self, principal: Principal, object_id: str) -> dict[str, Any] | None: ...


class MarketPort(Protocol):
    def search(self, principal: Principal, query: str) -> list[dict[str, Any]]: ...


class GrowthPort(Protocol):
    def evidence(self, principal: Principal, skill_id: str) -> list[dict[str, Any]]: ...


class RecruitingPort(Protocol):
    def draft(self, principal: Principal, job_id: str) -> dict[str, Any]: ...


class AgentPort(Protocol):
    def enqueue(self, principal: Principal, kind: str, key: str) -> str: ...


class GovernancePort(Protocol):
    def allowed(self, principal: Principal, source_id: str, operation: str) -> bool: ...


class LegacyReader(Protocol):
    def read_for_owner(self, owner_key: str, object_id: str) -> dict[str, Any] | None: ...


@dataclass
class ScopedProfileAdapter:
    """Legacy calls require a server-owned mapping; never accept client owner IDs."""

    reader: LegacyReader
    owners: dict[tuple[str, str], str]
    object_owners: dict[str, tuple[str, str]]

    def read(self, principal: Principal, object_id: str) -> dict[str, Any] | None:
        authorize(principal, "private.read", self.object_owners.get(object_id))
        owner = self.owners.get((principal.tenant_id, principal.user_id))
        if owner is None:
            return None
        return self.reader.read_for_owner(owner, object_id)
