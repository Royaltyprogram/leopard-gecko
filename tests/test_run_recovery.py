import json
from pathlib import Path

from leopard_gecko.adapters.base import WorkerPort, WorkerRunState, WorkerSubmission
from leopard_gecko.adapters.codex import CodexAdapter
from leopard_gecko.models.session import SessionStatus, TaskHistoryStatus
from leopard_gecko.models.task import QueueStatus
from leopard_gecko.orchestrator.pipeline import Orchestrator
from leopard_gecko.router.policy import RouteAction, RouteDecision


class RecoveryRouter:
    kind = "recovery-router"
    history_limit = 1

    def decide(self, *, task, config, sessions, global_queue_size) -> RouteDecision:
        del task, config, sessions, global_queue_size
        return RouteDecision(
            action=RouteAction.CREATE_NEW_SESSION,
            reason="forced for recovery test",
        )


class RecoveryNotePort:
    kind = "recovery-note"

    def make_note(self, user_prompt: str) -> str:
        return f"note::{user_prompt}"


class ManualRecoveryWorker(WorkerPort):
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
        del cwd, user_prompt
        submission = WorkerSubmission(
            session_id=session_id,
            task_id=task_id,
            run_id=f"run:{task_id}",
            process_id=1234,
            worker_context_id=worker_context_id or "ctx_initial",
            output_path=str(data_dir / f"{task_id}.jsonl"),
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
        del process_id, output_path
        return WorkerRunState(
            run_id=run_id,
            is_running=False,
            last_message="partial output",
            worker_context_id="ctx_recovered",
            requires_manual_recovery=True,
            recovery_reason="missing_exit_metadata",
        )


def test_codex_adapter_recovers_exit_state_from_disk(tmp_path, monkeypatch) -> None:
    adapter = CodexAdapter()
    output_path = _prepare_run_files(
        root=tmp_path,
        session_id="sess_1",
        task_id="task_1",
        meta_payload={
            "run_id": "codex:sess_1:task_1",
            "task_id": "task_1",
            "session_id": "sess_1",
            "pid": 999999,
            "started_at": "2026-04-01T00:00:00+00:00",
            "worker_context_id": "ctx_meta",
            "output_path": str(tmp_path / "worker_runs" / "sess_1" / "task_1.jsonl"),
            "status": "running",
        },
        exit_payload={
            "exit_code": 0,
            "finished_at": "2026-04-01T00:10:00+00:00",
        },
        output_lines=[
            json.dumps({"data": {"thread_id": "ctx_output"}}),
        ],
        last_message="done",
    )
    monkeypatch.setattr(CodexAdapter, "_is_process_running", staticmethod(lambda pid: False))

    state = adapter.poll(run_id=None, process_id=None, output_path=output_path)

    assert state.run_id == "codex:sess_1:task_1"
    assert state.is_running is False
    assert state.exit_code == 0
    assert state.worker_context_id == "ctx_output"
    assert state.last_message == "done"
    assert state.requires_manual_recovery is False


def test_codex_adapter_marks_unknown_terminated_run_for_manual_recovery(tmp_path, monkeypatch) -> None:
    adapter = CodexAdapter()
    output_path = _prepare_run_files(
        root=tmp_path,
        session_id="sess_1",
        task_id="task_2",
        meta_payload={
            "run_id": "codex:sess_1:task_2",
            "task_id": "task_2",
            "session_id": "sess_1",
            "pid": 999999,
            "started_at": "2026-04-01T00:00:00+00:00",
            "worker_context_id": "ctx_meta",
            "output_path": str(tmp_path / "worker_runs" / "sess_1" / "task_2.jsonl"),
            "status": "running",
        },
        output_lines=[],
        last_message="partial",
    )
    monkeypatch.setattr(CodexAdapter, "_is_process_running", staticmethod(lambda pid: False))

    state = adapter.poll(run_id=None, process_id=None, output_path=output_path)

    assert state.run_id == "codex:sess_1:task_2"
    assert state.is_running is False
    assert state.exit_code is None
    assert state.last_message == "partial"
    assert state.requires_manual_recovery is True
    assert state.recovery_reason == "missing_exit_metadata"


def test_poll_runs_blocks_session_when_run_exit_cannot_be_recovered(tmp_path) -> None:
    worker = ManualRecoveryWorker()
    orchestrator = Orchestrator(
        data_dir=str(tmp_path / ".leopard-gecko"),
        worker=worker,
        task_note_port=RecoveryNotePort(),
        router=RecoveryRouter(),
    )

    submission = orchestrator.submit("finish this carefully")
    poll_result = orchestrator.poll_runs()
    session = orchestrator.load_sessions().sessions[0]

    assert poll_result.running == 0
    assert poll_result.completed == 0
    assert poll_result.failed == 0
    assert poll_result.dispatched == 0
    assert session.status is SessionStatus.BLOCKED
    assert session.current_task_id == submission.task_id
    assert session.active_run_id is None
    assert session.active_pid is None
    assert session.worker_context_id == "ctx_recovered"
    assert _history_status(session, submission.task_id) is TaskHistoryStatus.INTERRUPTED
    assert orchestrator.task_repo.load(submission.task_id).queue_status is QueueStatus.FAILED


def _prepare_run_files(
    *,
    root: Path,
    session_id: str,
    task_id: str,
    meta_payload: dict,
    exit_payload: dict | None = None,
    output_lines: list[str],
    last_message: str | None = None,
) -> Path:
    run_dir = root / "worker_runs" / session_id
    run_dir.mkdir(parents=True, exist_ok=True)

    output_path = run_dir / f"{task_id}.jsonl"
    output_path.write_text("\n".join(output_lines) + ("\n" if output_lines else ""), encoding="utf-8")
    (run_dir / f"{task_id}.meta.json").write_text(
        json.dumps(meta_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if exit_payload is not None:
        (run_dir / f"{task_id}.exit.json").write_text(
            json.dumps(exit_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    if last_message is not None:
        (run_dir / f"{task_id}.last_message.txt").write_text(last_message, encoding="utf-8")
    return output_path


def _history_status(session, task_id: str) -> TaskHistoryStatus:
    for entry in session.task_history:
        if entry.task_id == task_id:
            return entry.status
    raise AssertionError(f"missing history for {task_id}")
