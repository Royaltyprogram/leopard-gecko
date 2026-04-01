from collections.abc import Sequence
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, Field

from leopard_gecko.models.config import AppConfig
from leopard_gecko.models.session import Session, SessionStatus, TaskHistoryEntry
from leopard_gecko.models.task import Task


class RoutingError(RuntimeError):
    """Raised when a router cannot produce a valid routing decision."""


class RouteAction(StrEnum):
    ASSIGN_EXISTING = "assign_existing"
    CREATE_NEW_SESSION = "create_new_session"
    ENQUEUE_GLOBAL = "enqueue_global"


class SessionSnapshot(BaseModel):
    session_id: str
    status: SessionStatus
    current_task_id: str | None = None
    queue_size: int = 0
    recent_history: list[TaskHistoryEntry] = Field(default_factory=list)
    recent_summary: str | None = None


class RouteDecision(BaseModel):
    action: RouteAction
    session_id: str | None = None
    reason: str
    confidence: float | None = None


class ContextRouter(Protocol):
    kind: str
    history_limit: int

    def decide(
        self,
        *,
        task: Task,
        config: AppConfig,
        sessions: list[SessionSnapshot],
        global_queue_size: int,
    ) -> RouteDecision:
        """Choose how a task should be routed from lightweight session snapshots."""


def build_session_snapshots(
    sessions: Sequence[Session],
    *,
    history_limit: int,
) -> list[SessionSnapshot]:
    snapshots: list[SessionSnapshot] = []

    for session in sessions:
        recent_history = list(session.task_history[-history_limit:])
        recent_summary = next(
            (entry.summary for entry in reversed(recent_history) if entry.summary),
            None,
        )
        snapshots.append(
            SessionSnapshot(
                session_id=session.session_id,
                status=session.status,
                current_task_id=session.current_task_id,
                queue_size=len(session.queue),
                recent_history=recent_history,
                recent_summary=recent_summary,
            )
        )

    return snapshots
