import os
from pathlib import Path
import shutil
import time

import pytest

from leopard_gecko.models.session import SessionStatus, TaskHistoryStatus
from leopard_gecko.models.task import QueueStatus, RoutingDecision
from leopard_gecko.orchestrator.pipeline import Orchestrator
from leopard_gecko.router.policy import RouteAction, RouteDecision


pytestmark = pytest.mark.e2e


class StaticTaskNotePort:
    kind = "static-note"

    def make_note(self, user_prompt: str) -> str:
        return f"note::{user_prompt}"


class CreateSessionRouter:
    kind = "create-session-router"
    history_limit = 1

    def decide(self, *, task, config, sessions, global_queue_size) -> RouteDecision:
        del task, config, sessions, global_queue_size
        return RouteDecision(
            action=RouteAction.CREATE_NEW_SESSION,
            reason="force codex worker e2e path",
        )


@pytest.mark.skipif(
    os.getenv("RUN_CODEX_E2E") != "1",
    reason="Set RUN_CODEX_E2E=1 to execute the real Codex-backed E2E test.",
)
@pytest.mark.skipif(
    shutil.which("codex") is None,
    reason="codex CLI is not installed.",
)
def test_real_codex_worker_completes_task(tmp_path) -> None:
    data_dir = tmp_path / ".leopard-gecko"
    orchestrator = Orchestrator(
        data_dir=str(data_dir),
        worker_backend="codex",
        task_note_port=StaticTaskNotePort(),
        router=CreateSessionRouter(),
    )

    result = orchestrator.submit(
        "Reply with exactly OK. Do not modify any files or run any shell commands."
    )

    assert result.routing_decision is RoutingDecision.CREATED_NEW_SESSION
    assert result.queue_status is QueueStatus.RUNNING
    assert result.assigned_session_id is not None

    session_id = result.assigned_session_id
    session = orchestrator.load_sessions().sessions[0]
    assert session.session_id == session_id
    assert session.status is SessionStatus.BUSY
    assert session.current_task_id == result.task_id
    assert session.active_run_id is not None
    assert session.last_run_output_path is not None

    output_path = Path(session.last_run_output_path)
    assert output_path.exists()
    assert output_path.parent == data_dir / "worker_runs" / session_id

    deadline = time.monotonic() + 90
    last_poll_result = None
    while time.monotonic() < deadline:
        last_poll_result = orchestrator.poll_runs()
        task = orchestrator.task_repo.load(result.task_id)
        if task.queue_status in {QueueStatus.COMPLETED, QueueStatus.FAILED}:
            break
        time.sleep(0.5)

    assert last_poll_result is not None

    task = orchestrator.task_repo.load(result.task_id)
    session = orchestrator.load_sessions().sessions[0]

    assert task.routing.decision is RoutingDecision.CREATED_NEW_SESSION
    assert task.queue_status is QueueStatus.COMPLETED
    assert session.status in {SessionStatus.COOLDOWN, SessionStatus.IDLE}
    assert session.current_task_id is None
    assert session.active_run_id is None
    assert session.active_pid is None
    assert session.worker_context_id
    assert len(session.task_history) == 1
    assert session.task_history[0].task_id == result.task_id
    assert session.task_history[0].status is TaskHistoryStatus.COMPLETED
    assert session.task_history[0].summary is not None
    assert "OK" in session.task_history[0].summary

    exit_path = output_path.with_name(f"{output_path.stem}.exit.json")
    state_path = output_path.with_name(f"{output_path.stem}.state.json")
    last_message_path = output_path.with_name(f"{output_path.stem}.last_message.txt")

    assert exit_path.exists()
    assert state_path.exists()
    assert last_message_path.exists()

    event_types = [event.event_type for event in orchestrator.tasks_log.read_all()]
    assert event_types == [
        "task_created",
        "task_routed",
        "task_dispatched",
        "task_completed",
    ]
