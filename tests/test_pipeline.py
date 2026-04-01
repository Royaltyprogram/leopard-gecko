from pathlib import Path
import re

import pytest

from leopard_gecko.adapters.base import WorkerPort, WorkerRunState, WorkerSubmission
from leopard_gecko.models.config import AppConfig
from leopard_gecko.models.session import SessionStatus, TaskHistoryStatus
from leopard_gecko.models.task import QueueStatus, RoutingDecision
from leopard_gecko.orchestrator.pipeline import Orchestrator
from leopard_gecko.router.policy import RouteAction, RouteDecision, RoutingError, SessionSnapshot


class FakeWorkerAdapter(WorkerPort):
    def __init__(self) -> None:
        self.submissions: list[WorkerSubmission] = []
        self.received_context_ids: list[str | None] = []
        self.received_prompts: list[str] = []
        self.poll_states: dict[str, WorkerRunState] = {}

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
        run_id = f"run_{len(self.submissions) + 1}"
        self.received_prompts.append(user_prompt)
        submission = WorkerSubmission(
            session_id=session_id,
            task_id=task_id,
            run_id=run_id,
            process_id=1000 + len(self.submissions),
            worker_context_id=worker_context_id or f"ctx_{session_id}",
            output_path=str(data_dir / f"{task_id}.jsonl"),
        )
        self.received_context_ids.append(worker_context_id)
        self.submissions.append(submission)
        self.poll_states.setdefault(run_id, WorkerRunState(run_id=run_id, is_running=True))
        return submission

    def poll(
        self,
        *,
        run_id: str | None,
        process_id: int | None,
        output_path: Path | None,
    ) -> WorkerRunState:
        if run_id is None:
            raise AssertionError("run_id is required in fake worker tests")
        return self.poll_states[run_id]

    def set_poll_state(
        self,
        run_id: str,
        *,
        is_running: bool,
        exit_code: int | None = None,
        worker_context_id: str | None = None,
        last_message: str | None = None,
    ) -> None:
        self.poll_states[run_id] = WorkerRunState(
            run_id=run_id,
            is_running=is_running,
            exit_code=exit_code,
            worker_context_id=worker_context_id,
            last_message=last_message,
        )


class FakeTaskNotePort:
    kind = "fake-note"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def make_note(self, user_prompt: str) -> str:
        self.calls.append(user_prompt)
        return f"n::{user_prompt}"


class FakeRouter:
    kind = "fake-router"
    history_limit = 2

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def decide(
        self,
        *,
        task,
        config,
        sessions: list[SessionSnapshot],
        global_queue_size: int,
    ) -> RouteDecision:
        self.calls.append(
            {
                "task": task,
                "config": config,
                "sessions": sessions,
                "global_queue_size": global_queue_size,
            }
        )
        return RouteDecision(
            action=RouteAction.CREATE_NEW_SESSION,
            reason="forced for test",
        )


class ScenarioRouter:
    kind = "scenario-router"
    history_limit = 5

    def decide(
        self,
        *,
        task,
        config,
        sessions: list[SessionSnapshot],
        global_queue_size: int,
    ) -> RouteDecision:
        del global_queue_size

        task_tokens = _tokens(f"{task.user_prompt} {task.task_note}")
        queue_limit = config.queue_policy.max_queue_per_session

        best_session: SessionSnapshot | None = None
        best_score = 0
        for session in sessions:
            if session.status is SessionStatus.DEAD:
                continue
            if session.queue_size >= queue_limit:
                continue

            session_tokens = set()
            for entry in session.recent_history:
                session_tokens |= _tokens(entry.user_prompt)
                session_tokens |= _tokens(entry.task_note)

            score = len(task_tokens & session_tokens)
            if score > best_score:
                best_score = score
                best_session = session

        if best_session is not None and best_score >= 2:
            return RouteDecision(
                action=RouteAction.ASSIGN_EXISTING,
                session_id=best_session.session_id,
                reason=f"matched_for_test score={best_score}",
                confidence=0.9,
            )

        live_sessions = [session for session in sessions if session.status is not SessionStatus.DEAD]
        if len(live_sessions) < config.max_terminal_num:
            return RouteDecision(
                action=RouteAction.CREATE_NEW_SESSION,
                reason="new_session_for_test",
                confidence=0.8,
            )

        return RouteDecision(
            action=RouteAction.ENQUEUE_GLOBAL,
            reason="global_queue_for_test",
            confidence=0.7,
        )


def test_submit_creates_then_reuses_related_session(tmp_path) -> None:
    worker = FakeWorkerAdapter()
    task_note_port = FakeTaskNotePort()
    orchestrator = Orchestrator(
        data_dir=str(tmp_path / ".leopard-gecko"),
        worker=worker,
        task_note_port=task_note_port,
        router=ScenarioRouter(),
    )

    first = orchestrator.submit("admin users pagination 추가해줘")
    second = orchestrator.submit("admin users pagination 버튼 스타일도 맞춰줘")

    assert first.routing_decision is RoutingDecision.CREATED_NEW_SESSION
    assert first.queue_status is QueueStatus.RUNNING
    assert first.assigned_session_id is not None

    assert second.routing_decision is RoutingDecision.ASSIGNED_EXISTING
    assert second.queue_status is QueueStatus.QUEUED_IN_SESSION
    assert second.assigned_session_id == first.assigned_session_id

    sessions_state = orchestrator.load_sessions()
    session = sessions_state.sessions[0]

    assert len(sessions_state.sessions) == 1
    assert session.queue == [second.task_id]
    assert session.active_run_id == worker.submissions[0].run_id
    assert session.active_pid == worker.submissions[0].process_id
    assert session.worker_context_id == worker.submissions[0].worker_context_id
    assert len(worker.submissions) == 1
    assert worker.received_context_ids == [None]


def test_poll_completion_dispatches_next_queued_task(tmp_path) -> None:
    worker = FakeWorkerAdapter()
    task_note_port = FakeTaskNotePort()
    orchestrator = Orchestrator(
        data_dir=str(tmp_path / ".leopard-gecko"),
        worker=worker,
        task_note_port=task_note_port,
        router=ScenarioRouter(),
    )

    first = orchestrator.submit("admin users pagination 추가해줘")
    second = orchestrator.submit("admin users pagination 버튼 스타일도 맞춰줘")
    worker.set_poll_state(
        worker.submissions[0].run_id,
        is_running=False,
        exit_code=0,
        worker_context_id="ctx_updated",
        last_message="done",
    )

    poll_result = orchestrator.poll_runs()
    session = orchestrator.load_sessions().sessions[0]

    assert poll_result.completed == 1
    assert poll_result.dispatched == 1
    assert session.current_task_id == second.task_id
    assert session.queue == []
    assert session.worker_context_id == "ctx_updated"
    assert session.active_run_id == worker.submissions[1].run_id
    assert worker.received_context_ids == [None, "ctx_updated"]
    assert _history_status(session, first.task_id) is TaskHistoryStatus.COMPLETED
    assert _history_status(session, second.task_id) is TaskHistoryStatus.RUNNING


def test_poll_completion_promotes_session_queue_from_task_snapshot_when_log_is_missing(tmp_path) -> None:
    worker = FakeWorkerAdapter()
    orchestrator = Orchestrator(
        data_dir=str(tmp_path / ".leopard-gecko"),
        worker=worker,
        task_note_port=FakeTaskNotePort(),
        router=ScenarioRouter(),
    )

    first = orchestrator.submit("admin users pagination 추가해줘")
    second = orchestrator.submit("admin users pagination 버튼 스타일도 맞춰줘")
    orchestrator.paths.tasks_log_path.unlink()
    worker.set_poll_state(
        worker.submissions[0].run_id,
        is_running=False,
        exit_code=0,
        worker_context_id="ctx_updated",
        last_message="done",
    )

    poll_result = orchestrator.poll_runs()
    session = orchestrator.load_sessions().sessions[0]

    assert first.task_id != second.task_id
    assert poll_result.completed == 1
    assert poll_result.dispatched == 1
    assert session.current_task_id == second.task_id
    assert worker.submissions[1].task_id == second.task_id
    assert worker.received_context_ids == [None, "ctx_updated"]


def test_load_task_falls_back_to_log_and_backfills_snapshot(tmp_path) -> None:
    worker = FakeWorkerAdapter()
    orchestrator = Orchestrator(
        data_dir=str(tmp_path / ".leopard-gecko"),
        worker=worker,
        task_note_port=FakeTaskNotePort(),
        router=ScenarioRouter(),
    )

    result = orchestrator.submit("payments export 기능 추가해줘")
    snapshot_path = orchestrator.paths.tasks_dir / f"{result.task_id}.json"
    snapshot_path.unlink()

    restored = orchestrator._load_task(result.task_id)

    assert restored.task_id == result.task_id
    assert restored.user_prompt == "payments export 기능 추가해줘"
    assert snapshot_path.exists()


def test_poll_failure_still_dispatches_next_queued_task(tmp_path) -> None:
    worker = FakeWorkerAdapter()
    task_note_port = FakeTaskNotePort()
    orchestrator = Orchestrator(
        data_dir=str(tmp_path / ".leopard-gecko"),
        worker=worker,
        task_note_port=task_note_port,
        router=ScenarioRouter(),
    )

    first = orchestrator.submit("admin users pagination 추가해줘")
    second = orchestrator.submit("admin users pagination 버튼 스타일도 맞춰줘")
    worker.set_poll_state(
        worker.submissions[0].run_id,
        is_running=False,
        exit_code=1,
        worker_context_id="ctx_failed",
        last_message="boom",
    )

    poll_result = orchestrator.poll_runs()
    session = orchestrator.load_sessions().sessions[0]

    assert poll_result.failed == 1
    assert poll_result.dispatched == 1
    assert session.current_task_id == second.task_id
    assert session.worker_context_id == "ctx_failed"
    assert worker.received_context_ids == [None, "ctx_failed"]
    assert _history_status(session, first.task_id) is TaskHistoryStatus.FAILED
    assert _history_status(session, second.task_id) is TaskHistoryStatus.RUNNING


def test_poll_running_updates_worker_context(tmp_path) -> None:
    worker = FakeWorkerAdapter()
    task_note_port = FakeTaskNotePort()
    orchestrator = Orchestrator(
        data_dir=str(tmp_path / ".leopard-gecko"),
        worker=worker,
        task_note_port=task_note_port,
        router=ScenarioRouter(),
    )

    first = orchestrator.submit("admin users pagination 추가해줘")
    worker.set_poll_state(
        worker.submissions[0].run_id,
        is_running=True,
        worker_context_id="ctx_running",
    )

    poll_result = orchestrator.poll_runs()
    session = orchestrator.load_sessions().sessions[0]

    assert poll_result.running == 1
    assert session.current_task_id == first.task_id
    assert session.worker_context_id == "ctx_running"
    assert session.active_run_id == worker.submissions[0].run_id


def test_idle_session_promotes_global_queue(tmp_path) -> None:
    worker = FakeWorkerAdapter()
    task_note_port = FakeTaskNotePort()
    orchestrator = Orchestrator(
        data_dir=str(tmp_path / ".leopard-gecko"),
        worker=worker,
        task_note_port=task_note_port,
        router=ScenarioRouter(),
    )
    config = AppConfig.default(str(tmp_path / ".leopard-gecko")).model_copy(update={"max_terminal_num": 1})
    orchestrator.config_repo.save(config)

    orchestrator.submit("admin users pagination 추가해줘")
    second = orchestrator.submit("payments export 기능 추가해줘")
    worker.set_poll_state(
        worker.submissions[0].run_id,
        is_running=False,
        exit_code=0,
        worker_context_id="ctx_done",
        last_message="done",
    )

    poll_result = orchestrator.poll_runs()
    session = orchestrator.load_sessions().sessions[0]

    assert second.queue_status is QueueStatus.QUEUED_GLOBALLY
    assert poll_result.completed == 1
    assert poll_result.dispatched == 1
    assert session.current_task_id == second.task_id
    assert session.status == "busy"
    assert orchestrator.load_sessions().global_queue == []
    assert worker.received_context_ids == [None, "ctx_done"]
    assert _history_status(session, second.task_id) is TaskHistoryStatus.RUNNING


def test_global_queue_stays_waiting_when_capacity_is_full(tmp_path) -> None:
    worker = FakeWorkerAdapter()
    task_note_port = FakeTaskNotePort()
    orchestrator = Orchestrator(
        data_dir=str(tmp_path / ".leopard-gecko"),
        worker=worker,
        task_note_port=task_note_port,
        router=ScenarioRouter(),
    )
    config = AppConfig.default(str(tmp_path / ".leopard-gecko")).model_copy(update={"max_terminal_num": 1})
    orchestrator.config_repo.save(config)

    orchestrator.submit("admin users pagination 추가해줘")
    second = orchestrator.submit("payments export 기능 추가해줘")

    promoted = orchestrator._promote_next_global_task(config)
    sessions_state = orchestrator.load_sessions()

    assert second.queue_status is QueueStatus.QUEUED_GLOBALLY
    assert promoted is False
    assert len(worker.submissions) == 1
    assert len(sessions_state.sessions) == 1
    assert sessions_state.sessions[0].current_task_id != second.task_id
    assert sessions_state.global_queue == [second.task_id]


def test_dead_session_allows_global_queue_promotion_into_new_session(tmp_path) -> None:
    worker = FakeWorkerAdapter()
    task_note_port = FakeTaskNotePort()
    orchestrator = Orchestrator(
        data_dir=str(tmp_path / ".leopard-gecko"),
        worker=worker,
        task_note_port=task_note_port,
        router=ScenarioRouter(),
    )
    config = AppConfig.default(str(tmp_path / ".leopard-gecko")).model_copy(update={"max_terminal_num": 1})
    orchestrator.config_repo.save(config)

    first = orchestrator.submit("admin users pagination 추가해줘")
    second = orchestrator.submit("payments export 기능 추가해줘")

    def mark_session_dead(state) -> None:
        session = state.sessions[0]
        session.status = SessionStatus.DEAD
        session.current_task_id = None
        session.active_run_id = None
        session.active_pid = None
        session.active_run_started_at = None
        session.last_run_output_path = None

    orchestrator.sessions_repo.update(mark_session_dead)

    promoted = orchestrator._promote_next_global_task(config)
    sessions_state = orchestrator.load_sessions()
    dead_session = sessions_state.sessions[0]
    new_session = sessions_state.sessions[1]
    promotion_event = next(
        event
        for event in reversed(orchestrator.tasks_log.read_all())
        if event.event_type == "task_promoted_from_queue" and event.task_id == second.task_id
    )

    assert first.routing_decision is RoutingDecision.CREATED_NEW_SESSION
    assert second.queue_status is QueueStatus.QUEUED_GLOBALLY
    assert promoted is True
    assert len(worker.submissions) == 2
    assert dead_session.status is SessionStatus.DEAD
    assert new_session.session_id != dead_session.session_id
    assert new_session.current_task_id == second.task_id
    assert sessions_state.global_queue == []
    assert _history_status(new_session, second.task_id) is TaskHistoryStatus.RUNNING
    assert promotion_event.payload == {
        "session_id": new_session.session_id,
        "source": "global",
        "created_session": True,
    }


def test_submit_persists_task_before_sessions_mutation(tmp_path, monkeypatch) -> None:
    worker = FakeWorkerAdapter()
    orchestrator = Orchestrator(
        data_dir=str(tmp_path / ".leopard-gecko"),
        worker=worker,
        task_note_port=FakeTaskNotePort(),
        router=FakeRouter(),
    )
    task_id = "task_persisted"

    monkeypatch.setattr(
        "leopard_gecko.orchestrator.pipeline._generate_prefixed_id",
        lambda prefix: task_id if prefix == "task" else "sess_unused",
    )

    def fail_update(mutator):
        del mutator
        raise RuntimeError("sessions update failed")

    monkeypatch.setattr(orchestrator.sessions_repo, "update", fail_update)

    with pytest.raises(RuntimeError, match="sessions update failed"):
        orchestrator.submit("admin users pagination 추가해줘")

    restored = orchestrator.task_repo.load(task_id)
    assert restored.task_id == task_id
    assert restored.user_prompt == "admin users pagination 추가해줘"
    events = orchestrator.tasks_log.read_all()
    assert len(events) == 1
    assert events[0].event_type == "task_created"
    assert events[0].task_id == task_id


def test_load_task_uses_snapshot_when_task_created_log_is_missing(tmp_path) -> None:
    worker = FakeWorkerAdapter()
    task_note_port = FakeTaskNotePort()
    orchestrator = Orchestrator(
        data_dir=str(tmp_path / ".leopard-gecko"),
        worker=worker,
        task_note_port=task_note_port,
        router=ScenarioRouter(),
    )

    first = orchestrator.submit("admin users pagination 추가해줘")
    second = orchestrator.submit("admin users pagination 버튼 스타일도 맞춰줘")
    orchestrator.paths.tasks_log_path.write_text("", encoding="utf-8")
    worker.set_poll_state(
        worker.submissions[0].run_id,
        is_running=False,
        exit_code=0,
        worker_context_id="ctx_updated",
        last_message="done",
    )

    poll_result = orchestrator.poll_runs()
    session = orchestrator.load_sessions().sessions[0]

    assert first.task_id != second.task_id
    assert poll_result.completed == 1
    assert poll_result.dispatched == 1
    assert session.current_task_id == second.task_id
    assert worker.received_context_ids == [None, "ctx_updated"]
    assert _history_status(session, second.task_id) is TaskHistoryStatus.RUNNING


def test_submit_uses_injected_note_port_and_router(tmp_path) -> None:
    worker = FakeWorkerAdapter()
    task_note_port = FakeTaskNotePort()
    router = FakeRouter()
    orchestrator = Orchestrator(
        data_dir=str(tmp_path / ".leopard-gecko"),
        worker=worker,
        task_note_port=task_note_port,
        router=router,
    )

    result = orchestrator.submit("  raw user prompt  ")

    assert result.routing_decision is RoutingDecision.CREATED_NEW_SESSION
    assert task_note_port.calls == ["raw user prompt"]
    assert worker.received_prompts == ["raw user prompt"]
    assert len(router.calls) == 1
    assert router.calls[0]["global_queue_size"] == 0
    routed_task = router.calls[0]["task"]
    assert routed_task.task_note == "n::raw user prompt"
    assert router.calls[0]["sessions"] == []


def test_submit_raises_when_router_returns_invalid_existing_session(tmp_path) -> None:
    worker = FakeWorkerAdapter()

    class InvalidRouter:
        kind = "invalid-router"
        history_limit = 1

        def decide(self, *, task, config, sessions, global_queue_size) -> RouteDecision:
            del task, config, sessions, global_queue_size
            return RouteDecision(
                action=RouteAction.ASSIGN_EXISTING,
                session_id="missing-session",
                reason="invalid for test",
            )

    orchestrator = Orchestrator(
        data_dir=str(tmp_path / ".leopard-gecko"),
        worker=worker,
        task_note_port=FakeTaskNotePort(),
        router=InvalidRouter(),
    )

    with pytest.raises(RoutingError, match="unknown session_id"):
        orchestrator.submit("admin users pagination 추가해줘")


def test_submit_raises_when_default_task_note_generator_cannot_run(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    worker = FakeWorkerAdapter()
    orchestrator = Orchestrator(
        data_dir=str(tmp_path / ".leopard-gecko"),
        worker=worker,
        router=FakeRouter(),
    )

    with pytest.raises(RoutingError, match="Missing OpenAI API key"):
        orchestrator.submit("admin users pagination 추가해줘")


def _history_status(session, task_id: str) -> TaskHistoryStatus:
    for entry in session.task_history:
        if entry.task_id == task_id:
            return entry.status
    raise AssertionError(f"missing history for {task_id}")


def _tokens(text: str) -> set[str]:
    return {token.lower() for token in re.findall(r"[\w-]{2,}", text)}
