from pathlib import Path
from typing import Protocol

from pydantic import BaseModel


class WorkerSubmission(BaseModel):
    session_id: str
    task_id: str
    accepted: bool = True
    run_id: str | None = None
    process_id: int | None = None
    worker_context_id: str | None = None
    output_path: str | None = None


class WorkerRunState(BaseModel):
    run_id: str | None = None
    is_running: bool
    exit_code: int | None = None
    worker_context_id: str | None = None
    last_message: str | None = None
    requires_manual_recovery: bool = False
    recovery_reason: str | None = None


class WorkerPort(Protocol):
    def submit(
        self,
        session_id: str,
        task_id: str,
        user_prompt: str,
        *,
        cwd: Path,
        data_dir: Path,
        worker_context_id: str | None = None,
    ) -> WorkerSubmission:
        """Submit a raw user prompt to an assigned worker session."""

    def poll(
        self,
        *,
        run_id: str | None,
        process_id: int | None,
        output_path: Path | None,
    ) -> WorkerRunState:
        """Inspect the current execution state for a worker run."""
