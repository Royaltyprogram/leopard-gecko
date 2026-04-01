from pathlib import Path

from leopard_gecko.adapters.base import WorkerPort, WorkerRunState, WorkerSubmission


class NoopWorkerAdapter(WorkerPort):
    def __init__(self) -> None:
        self.submissions: list[WorkerSubmission] = []

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
        run_id = f"noop:{task_id}"
        submission = WorkerSubmission(
            session_id=session_id,
            task_id=task_id,
            run_id=run_id,
            worker_context_id=worker_context_id or f"noop:{session_id}",
        )
        self.submissions.append(submission)
        return submission

    def poll(
        self,
        *,
        run_id: str | None,
        process_id: int | None,
        output_path: Path | None,
    ) -> WorkerRunState:
        return WorkerRunState(
            run_id=run_id,
            is_running=False,
            exit_code=0,
            last_message="noop worker completed",
        )
