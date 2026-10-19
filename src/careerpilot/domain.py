"""Pure domain contracts: no HTTP, ORM or third-party runtime imports."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Principal:
    user_id: str
    tenant_id: str


class MemoryPort(Protocol):
    """Every private adapter operation requires a trusted identity context."""

    def read(self, principal: Principal, memory_id: str) -> dict | None: ...


class DomainError(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)
