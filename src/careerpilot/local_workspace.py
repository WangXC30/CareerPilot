"""One persistent personal workspace; no credentials or operational roles."""

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from careerpilot.domain import DomainError, Principal
from careerpilot.persistence import LocalWorkspace, User, new_id


def resolve(db: Session) -> Principal:
    current = db.get(LocalWorkspace, "personal")
    if current is not None:
        from careerpilot.v1_models import DeletionRequest

        pending = db.scalar(
            select(DeletionRequest.id).where(
                DeletionRequest.user_id == current.user_id,
                DeletionRequest.tenant_id == current.tenant_id,
                DeletionRequest.status == "queued",
            )
        )
        if pending:
            raise DomainError("LOCAL_WORKSPACE_RESETTING", "本地资料正在清理中，请稍后重试。", 409)
        return Principal(current.user_id, current.tenant_id)
    db.rollback()
    # Serialize the first selection across API processes, including fresh databases.
    if db.bind is not None and db.bind.dialect.name == "sqlite":
        db.execute(text("BEGIN IMMEDIATE"))
    else:
        db.execute(text("LOCK TABLE local_workspace IN EXCLUSIVE MODE"))
    current = db.get(LocalWorkspace, "personal")
    if current is None:
        users = list(db.scalars(select(User).order_by(User.created_at, User.id)))
        if len(users) > 1:
            raise DomainError(
                "LOCAL_WORKSPACE_AMBIGUOUS",
                "发现多份历史资料，请先明确保留哪份个人工作区；系统不会自动合并或覆盖。",
                409,
            )
        owner = users[0] if users else User(id=new_id(), tenant_id=new_id(), display_name="我")
        if not users:
            db.add(owner)
            db.flush()
        current = LocalWorkspace(id="personal", user_id=owner.id, tenant_id=owner.tenant_id)
        db.add(current)
    principal = Principal(current.user_id, current.tenant_id)
    db.commit()
    return principal


def principal(engine) -> Principal:
    with Session(engine) as db:
        return resolve(db)
