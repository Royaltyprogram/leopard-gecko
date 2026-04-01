from datetime import datetime, timezone
from enum import StrEnum

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
    terminal_id: str | None = None
    status: SessionStatus = SessionStatus.IDLE
    current_task_id: str | None = None
    queue: list[str] = Field(default_factory=list)
    task_history: list[TaskHistoryEntry] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_heartbeat: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SessionsState(BaseModel):
    sessions: list[Session] = Field(default_factory=list)
    global_queue: list[str] = Field(default_factory=list)

