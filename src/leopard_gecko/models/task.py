from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class QueueStatus(StrEnum):
    PENDING = "pending"
    QUEUED_IN_SESSION = "queued_in_session"
    QUEUED_GLOBALLY = "queued_globally"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class RoutingDecision(StrEnum):
    PENDING = "pending"
    ASSIGNED_EXISTING = "assigned_existing"
    CREATED_NEW_SESSION = "created_new_session"
    ENQUEUED_GLOBAL = "enqueued_global"


class TaskRouting(BaseModel):
    assigned_session_id: str | None = None
    decision: RoutingDecision = RoutingDecision.PENDING
    reason: str | None = None


class Task(BaseModel):
    task_id: str
    user_prompt: str
    task_note: str
    routing: TaskRouting = Field(default_factory=TaskRouting)
    queue_status: QueueStatus = QueueStatus.PENDING
    retry_count: int = Field(default=0, ge=0)
    max_retries: int = Field(default=3, ge=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("user_prompt", "task_note")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized


class TaskEvent(BaseModel):
    event_type: str
    task_id: str
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    payload: dict[str, Any] = Field(default_factory=dict)
