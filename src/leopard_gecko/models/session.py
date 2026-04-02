from datetime import datetime, timezone
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, Field


class SessionStatus(StrEnum):
    IDLE = "idle"
    BUSY = "busy"
    BLOCKED = "blocked"
    DEAD = "dead"


class TaskHistoryStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class TaskHistoryEntry(BaseModel):
    task_id: str
    user_prompt: str
    task_note: str
    status: TaskHistoryStatus
    summary: str | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Session(BaseModel):
    session_id: str
    status: SessionStatus = SessionStatus.IDLE
    turn_count: int = 0
    current_task_id: str | None = None
    worker_backend: str | None = None
    worker_context_id: str | None = None
    worktree_path: str | None = None
    worktree_branch: str | None = None
    worktree_base_ref: str | None = None
    active_run_id: str | None = None
    active_pid: int | None = None
    active_run_started_at: datetime | None = None
    last_run_output_path: str | None = None
    queue: list[str] = Field(default_factory=list)
    task_history: list[TaskHistoryEntry] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_heartbeat: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SessionsState(BaseModel):
    sessions: list[Session] = Field(default_factory=list)
    global_queue: list[str] = Field(default_factory=list)


class SessionStatusCarrier(Protocol):
    status: SessionStatus


def live_session_count(sessions: list[SessionStatusCarrier]) -> int:
    return sum(1 for session in sessions if session.status is not SessionStatus.DEAD)
