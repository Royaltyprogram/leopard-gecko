import json

import pytest
from typer.testing import CliRunner

from leopard_gecko.cli.main import app
from leopard_gecko.models.session import SessionStatus, TaskHistoryStatus
from leopard_gecko.models.task import QueueStatus, RoutingDecision
from leopard_gecko.orchestrator.pipeline import Orchestrator
from leopard_gecko.router.agent import AgentRouter
from leopard_gecko.router.policy import RouteAction, RouteDecision
from leopard_gecko.router.task_notes import AgentTaskNoteGenerator


pytestmark = pytest.mark.e2e

runner = CliRunner()


def test_cli_full_workflow_exercises_all_commands(monkeypatch, tmp_path) -> None:
    data_dir = tmp_path / ".leopard-gecko"

    monkeypatch.setattr(
        AgentTaskNoteGenerator,
        "make_note",
        lambda self, user_prompt: f"note::{user_prompt}",
    )

    def fake_decide(self, *, task, config, sessions, global_queue_size) -> RouteDecision:
        del config, global_queue_size
        if "global" in task.user_prompt:
            return RouteDecision(
                action=RouteAction.ENQUEUE_GLOBAL,
                reason="forced global queue for e2e",
                confidence=0.7,
            )
        if sessions:
            return RouteDecision(
                action=RouteAction.ASSIGN_EXISTING,
                session_id=sessions[0].session_id,
                reason="forced existing session for e2e",
                confidence=0.9,
            )
        return RouteDecision(
            action=RouteAction.CREATE_NEW_SESSION,
            reason="forced new session for e2e",
            confidence=0.95,
        )

    monkeypatch.setattr(AgentRouter, "decide", fake_decide)

    init_result = runner.invoke(
        app,
        ["init", "--data-dir", str(data_dir), "--worker-backend", "noop"],
    )
    assert init_result.exit_code == 0
    assert "Initialized Leopard Gecko" in init_result.stdout
    assert "worker_backend=noop" in init_result.stdout

    config_path = data_dir / "config.json"
    config_payload = json.loads(config_path.read_text(encoding="utf-8"))
    config_payload["max_terminal_num"] = 1
    config_path.write_text(json.dumps(config_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    status_result = runner.invoke(app, ["status", "--data-dir", str(data_dir)])
    assert status_result.exit_code == 0
    assert "sessions=0 busy=0 idle=0" in status_result.stdout
    assert "global_queue=0" in status_result.stdout

    first_submit = runner.invoke(app, ["submit", "first task", "--data-dir", str(data_dir)])
    assert first_submit.exit_code == 0
    assert "decision=created_new_session" in first_submit.stdout
    assert "queue_status=running" in first_submit.stdout
    first_lines = _parse_key_value_lines(first_submit.stdout)
    session_id = first_lines["assigned_session_id"]
    first_task_id = first_lines["task_id"]

    second_submit = runner.invoke(app, ["submit", "second task", "--data-dir", str(data_dir)])
    assert second_submit.exit_code == 0
    assert "decision=assigned_existing" in second_submit.stdout
    assert "queue_status=queued_in_session" in second_submit.stdout
    second_lines = _parse_key_value_lines(second_submit.stdout)
    second_task_id = second_lines["task_id"]
    assert second_lines["assigned_session_id"] == session_id

    third_submit = runner.invoke(app, ["submit", "global task", "--data-dir", str(data_dir)])
    assert third_submit.exit_code == 0
    assert "decision=enqueued_global" in third_submit.stdout
    assert "queue_status=queued_globally" in third_submit.stdout
    third_task_id = _parse_key_value_lines(third_submit.stdout)["task_id"]

    sessions_result = runner.invoke(app, ["sessions", "--data-dir", str(data_dir)])
    assert sessions_result.exit_code == 0
    assert "Sessions" in sessions_result.stdout
    assert "busy" in sessions_result.stdout

    busy_status = runner.invoke(app, ["status", "--data-dir", str(data_dir)])
    assert busy_status.exit_code == 0
    assert "sessions=1 busy=1 idle=0" in busy_status.stdout
    assert "global_queue=1" in busy_status.stdout

    orchestrator = Orchestrator(data_dir=str(data_dir))
    initial_state = orchestrator.load_sessions()
    assert len(initial_state.sessions) == 1
    assert initial_state.sessions[0].session_id == session_id
    assert initial_state.sessions[0].current_task_id == first_task_id
    assert initial_state.sessions[0].queue == [second_task_id]
    assert initial_state.global_queue == [third_task_id]
    assert orchestrator.task_repo.load(first_task_id).queue_status is QueueStatus.RUNNING
    assert orchestrator.task_repo.load(second_task_id).queue_status is QueueStatus.QUEUED_IN_SESSION
    assert orchestrator.task_repo.load(third_task_id).queue_status is QueueStatus.QUEUED_GLOBALLY

    first_poll = runner.invoke(app, ["poll", "--data-dir", str(data_dir)])
    assert first_poll.exit_code == 0
    assert "running=0" in first_poll.stdout
    assert "completed=1" in first_poll.stdout
    assert "failed=0" in first_poll.stdout
    assert "dispatched=1" in first_poll.stdout

    after_first_poll = orchestrator.load_sessions()
    session = after_first_poll.sessions[0]
    assert session.status is SessionStatus.BUSY
    assert session.current_task_id == second_task_id
    assert session.queue == []
    assert after_first_poll.global_queue == [third_task_id]
    assert orchestrator.task_repo.load(first_task_id).queue_status is QueueStatus.COMPLETED
    assert orchestrator.task_repo.load(second_task_id).queue_status is QueueStatus.RUNNING

    worker_once = runner.invoke(app, ["worker", "--data-dir", str(data_dir), "--once"])
    assert worker_once.exit_code == 0
    assert "completed=1" in worker_once.stdout
    assert "failed=0" in worker_once.stdout
    assert "dispatched=1" in worker_once.stdout

    after_worker_once = orchestrator.load_sessions()
    session = after_worker_once.sessions[0]
    assert session.status is SessionStatus.BUSY
    assert session.current_task_id == third_task_id
    assert after_worker_once.global_queue == []
    assert orchestrator.task_repo.load(second_task_id).queue_status is QueueStatus.COMPLETED
    assert orchestrator.task_repo.load(third_task_id).queue_status is QueueStatus.RUNNING

    final_poll = runner.invoke(app, ["poll", "--data-dir", str(data_dir)])
    assert final_poll.exit_code == 0
    assert "completed=1" in final_poll.stdout
    assert "failed=0" in final_poll.stdout
    assert "dispatched=0" in final_poll.stdout

    final_status = runner.invoke(app, ["status", "--data-dir", str(data_dir)])
    assert final_status.exit_code == 0
    assert "sessions=1 busy=0 idle=1" in final_status.stdout
    assert "blocked=0 dead=0" in final_status.stdout
    assert "global_queue=0" in final_status.stdout

    final_sessions = runner.invoke(app, ["sessions", "--data-dir", str(data_dir)])
    assert final_sessions.exit_code == 0
    assert "Sessions" in final_sessions.stdout
    assert "idle" in final_sessions.stdout

    final_state = orchestrator.load_sessions()
    assert len(final_state.sessions) == 1
    session = final_state.sessions[0]
    assert session.status is SessionStatus.IDLE
    assert session.current_task_id is None
    assert session.queue == []
    assert [entry.task_id for entry in session.task_history] == [
        first_task_id,
        second_task_id,
        third_task_id,
    ]
    assert [entry.status for entry in session.task_history] == [
        TaskHistoryStatus.COMPLETED,
        TaskHistoryStatus.COMPLETED,
        TaskHistoryStatus.COMPLETED,
    ]

    assert orchestrator.task_repo.load(first_task_id).routing.decision is RoutingDecision.CREATED_NEW_SESSION
    assert orchestrator.task_repo.load(second_task_id).routing.decision is RoutingDecision.ASSIGNED_EXISTING
    assert orchestrator.task_repo.load(third_task_id).routing.decision is RoutingDecision.ENQUEUED_GLOBAL
    assert orchestrator.task_repo.load(first_task_id).queue_status is QueueStatus.COMPLETED
    assert orchestrator.task_repo.load(second_task_id).queue_status is QueueStatus.COMPLETED
    assert orchestrator.task_repo.load(third_task_id).queue_status is QueueStatus.COMPLETED

    event_types = [event.event_type for event in orchestrator.tasks_log.read_all()]
    assert event_types.count("task_created") == 3
    assert event_types.count("task_routed") == 3
    assert event_types.count("task_dispatched") == 3
    assert event_types.count("task_completed") == 3
    assert event_types.count("task_promoted_from_queue") == 2


def _parse_key_value_lines(output: str) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        pairs[key] = value
    return pairs
