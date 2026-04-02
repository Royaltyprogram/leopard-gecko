from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import subprocess
import threading
import time

import pytest

from leopard_gecko.adapters.base import WorkerPort, WorkerRunState, WorkerSubmission
from leopard_gecko.models.config import AppConfig, WorkerBackend
from leopard_gecko.models.session import (
    Session,
    SessionsState,
    SessionStatus,
    TaskHistoryStatus,
)
from leopard_gecko.models.task import QueueStatus, RoutingDecision, Task, TaskRouting
from leopard_gecko.orchestrator.pipeline import Orchestrator
from leopard_gecko.router.policy import RouteAction, RouteDecision, RoutingError, SessionSnapshot


class FakeWorkerAdapter(WorkerPort):
    def __init__(self) -> None:
        self.submissions: list[WorkerSubmission] = []
        self.received_context_ids: list[str | None] = []
        self.received_prompts: list[str] = []
        self.received_cwds: list[Path] = []
        self.poll_states: dict[str, WorkerRunState] = {}
        self.submit_errors: list[Exception] = []

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
        if self.submit_errors:
            raise self.submit_errors.pop(0)
        run_id = f"run_{len(self.submissions) + 1}"
        self.received_prompts.append(user_prompt)
        self.received_cwds.append(cwd)
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

    def fail_next_submit(self, message: str = "submit failed") -> None:
        self.submit_errors.append(RuntimeError(message))


class SelectiveFailWorkerAdapter(FakeWorkerAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.fail_task_ids: set[str] = set()

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
        if task_id in self.fail_task_ids:
            raise RuntimeError(f"submit failed for {task_id}")
        return super().submit(
            session_id,
            task_id,
            user_prompt,
            cwd=cwd,
            data_dir=data_dir,
            worker_context_id=worker_context_id,
        )

class FakeTaskNotePort:
    kind = "fake-note"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def make_note(self, user_prompt: str) -> str:
        self.calls.append(user_prompt)
        return f"n::{user_prompt}"


class BlockingTaskNotePort:
    kind = "blocking-note"

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    def make_note(self, user_prompt: str) -> str:
        self.entered.set()
        if not self.release.wait(timeout=5):
            raise AssertionError("blocking note port was not released")
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
            )

        live_sessions = [session for session in sessions if session.status is not SessionStatus.DEAD]
        if len(live_sessions) < config.max_terminal_num:
            return RouteDecision(
                action=RouteAction.CREATE_NEW_SESSION,
                reason="new_session_for_test",
            )

        return RouteDecision(
            action=RouteAction.ENQUEUE_GLOBAL,
            reason="global_queue_for_test",
        )


class BlockingSecondDecisionRouter:
    kind = "blocking-second-decision-router"
    history_limit = 5

    def __init__(self) -> None:
        self._delegate = ScenarioRouter()
        self._call_count = 0
        self.entered = threading.Event()
        self.release = threading.Event()

    def decide(
        self,
        *,
        task,
        config,
        sessions: list[SessionSnapshot],
        global_queue_size: int,
    ) -> RouteDecision:
        self._call_count += 1
        if self._call_count == 2:
            self.entered.set()
            if not self.release.wait(timeout=5):
                raise AssertionError("blocking router was not released")
        return self._delegate.decide(
            task=task,
            config=config,
            sessions=sessions,
            global_queue_size=global_queue_size,
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

    first = orchestrator.submit("add admin users pagination")
    second = orchestrator.submit("also fix admin users pagination button style")

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
    assert worker.received_cwds == [orchestrator.cwd]
    assert _snapshot_status(orchestrator, first.task_id) is QueueStatus.RUNNING
    assert _snapshot_status(orchestrator, second.task_id) is QueueStatus.QUEUED_IN_SESSION


def test_submit_tracks_turn_count_for_session_assignments(tmp_path) -> None:
    worker = FakeWorkerAdapter()
    task_note_port = FakeTaskNotePort()
    orchestrator = Orchestrator(
        data_dir=str(tmp_path / ".leopard-gecko"),
        worker=worker,
        task_note_port=task_note_port,
        router=ScenarioRouter(),
    )

    first = orchestrator.submit("add admin users pagination")
    second = orchestrator.submit("also fix admin users pagination button style")
    session = orchestrator.load_sessions().sessions[0]

    assert session.session_id == first.assigned_session_id
    assert session.turn_count == 2
    assert session.queue == [second.task_id]


def test_submit_rejects_existing_session_at_turn_limit(tmp_path) -> None:
    worker = FakeWorkerAdapter()

    class ExistingSessionRouter:
        kind = "existing-session-router"
        history_limit = 1

        def decide(
            self,
            *,
            task,
            config,
            sessions: list[SessionSnapshot],
            global_queue_size: int,
        ) -> RouteDecision:
            del task, config, global_queue_size
            return RouteDecision(
                action=RouteAction.ASSIGN_EXISTING,
                session_id=sessions[0].session_id,
                reason="force existing session for test",
            )

    orchestrator = Orchestrator(
        data_dir=str(tmp_path / ".leopard-gecko"),
        worker=worker,
        task_note_port=FakeTaskNotePort(),
        router=ExistingSessionRouter(),
    )
    orchestrator.init_storage()
    orchestrator.sessions_repo.update(
        lambda state: state.sessions.append(
            Session(
                session_id="sess_full",
                status=SessionStatus.IDLE,
                turn_count=5,
            )
        )
    )

    with pytest.raises(RoutingError, match="turn limit"):
        orchestrator.submit("continue in the same session")


def test_submit_uses_session_worktree_when_enabled(tmp_path) -> None:
    repo_dir = _init_git_repo(tmp_path / "repo")
    worktree_root = tmp_path / "worktrees"
    worker = FakeWorkerAdapter()
    orchestrator = Orchestrator(
        cwd=repo_dir,
        data_dir=str(tmp_path / ".leopard-gecko"),
        worker=worker,
        task_note_port=FakeTaskNotePort(),
        router=ScenarioRouter(),
    )
    base_config = orchestrator.init_storage()
    config = base_config.model_copy(
        update={
            "worktree": base_config.worktree.model_copy(
                update={
                    "enabled": True,
                    "root_dir": str(worktree_root),
                }
            )
        }
    )
    orchestrator.config_repo.save(config)

    first = orchestrator.submit("add admin users pagination")
    session = orchestrator.load_sessions().sessions[0]

    assert session.session_id == first.assigned_session_id
    assert session.worktree_path == str(worktree_root / session.session_id)
    assert session.worktree_branch == f"lg/{session.session_id}"
    assert session.worktree_base_ref == "main"
    assert worker.received_cwds == [Path(session.worktree_path)]


def test_submit_reuses_idle_session_without_stale_backend_context(tmp_path) -> None:
    worker = FakeWorkerAdapter()

    class ExistingSessionRouter:
        kind = "existing-session-router"
        history_limit = 1

        def decide(
            self,
            *,
            task,
            config,
            sessions: list[SessionSnapshot],
            global_queue_size: int,
        ) -> RouteDecision:
            del task, config, global_queue_size
            return RouteDecision(
                action=RouteAction.ASSIGN_EXISTING,
                session_id=sessions[0].session_id,
                reason="reuse stale session for test",
            )

    orchestrator = Orchestrator(
        data_dir=str(tmp_path / ".leopard-gecko"),
        worker=worker,
        task_note_port=FakeTaskNotePort(),
        router=ExistingSessionRouter(),
    )
    base_config = orchestrator.init_storage()
    config = base_config.model_copy(
        update={
            "worker": base_config.worker.model_copy(update={"backend": WorkerBackend.CODEX})
        }
    )
    orchestrator.config_repo.save(config)

    orchestrator.sessions_repo.update(
        lambda state: state.sessions.append(
            Session(
                session_id="sess_stale",
                status=SessionStatus.IDLE,
                worker_backend="noop",
                worker_context_id="noop:sess_stale",
            )
        )
    )

    result = orchestrator.submit("create a simple html page")
    session = orchestrator.load_sessions().sessions[0]

    assert result.assigned_session_id == "sess_stale"
    assert worker.received_context_ids == [None]
    assert session.worker_backend == WorkerBackend.CODEX.value
    assert session.worker_context_id == worker.submissions[0].worker_context_id


def test_resolve_worker_rebuilds_cached_worker_after_backend_change(tmp_path, monkeypatch) -> None:
    orchestrator = Orchestrator(data_dir=str(tmp_path / ".leopard-gecko"))
    config = orchestrator.init_storage()
    seen: list[WorkerBackend] = []

    def fake_build_worker(config: AppConfig, backend_override=None):
        backend = backend_override or config.worker.backend
        seen.append(backend)
        return object()

    monkeypatch.setattr("leopard_gecko.orchestrator.pipeline.build_worker", fake_build_worker)

    first_worker = orchestrator._resolve_worker(config)
    updated_config = config.model_copy(
        update={
            "worker": config.worker.model_copy(update={"backend": WorkerBackend.CODEX})
        }
    )
    second_worker = orchestrator._resolve_worker(updated_config)

    assert first_worker is not second_worker
    assert seen == [WorkerBackend.NOOP, WorkerBackend.CODEX]


def test_poll_reuses_same_session_worktree_for_follow_up_dispatch(tmp_path) -> None:
    repo_dir = _init_git_repo(tmp_path / "repo")
    worktree_root = tmp_path / "worktrees"
    worker = FakeWorkerAdapter()
    orchestrator = Orchestrator(
        cwd=repo_dir,
        data_dir=str(tmp_path / ".leopard-gecko"),
        worker=worker,
        task_note_port=FakeTaskNotePort(),
        router=ScenarioRouter(),
    )
    base_config = orchestrator.init_storage()
    config = base_config.model_copy(
        update={
            "worktree": base_config.worktree.model_copy(
                update={
                    "enabled": True,
                    "root_dir": str(worktree_root),
                }
            )
        }
    )
    orchestrator.config_repo.save(config)

    first = orchestrator.submit("add admin users pagination")
    second = orchestrator.submit("also fix admin users pagination button style")
    worker.set_poll_state(
        worker.submissions[0].run_id,
        is_running=False,
        exit_code=0,
        worker_context_id="ctx_updated",
        last_message="done",
    )

    poll_result = orchestrator.poll_runs()
    session = orchestrator.load_sessions().sessions[0]

    assert first.assigned_session_id == second.assigned_session_id
    assert poll_result.dispatched == 1
    assert len(worker.received_cwds) == 2
    assert worker.received_cwds[0] == Path(session.worktree_path)
    assert worker.received_cwds[1] == Path(session.worktree_path)


def test_submit_dispatch_failure_removes_created_worktree_for_new_session(tmp_path) -> None:
    repo_dir = _init_git_repo(tmp_path / "repo")
    worktree_root = tmp_path / "worktrees"
    worker = FakeWorkerAdapter()
    worker.fail_next_submit("worker submit blew up")
    orchestrator = Orchestrator(
        cwd=repo_dir,
        data_dir=str(tmp_path / ".leopard-gecko"),
        worker=worker,
        task_note_port=FakeTaskNotePort(),
        router=FakeRouter(),
    )
    base_config = orchestrator.init_storage()
    config = base_config.model_copy(
        update={
            "worktree": base_config.worktree.model_copy(
                update={
                    "enabled": True,
                    "root_dir": str(worktree_root),
                }
            )
        }
    )
    orchestrator.config_repo.save(config)

    with pytest.raises(RuntimeError, match="worker submit blew up"):
        orchestrator.submit("add admin users pagination")

    assert orchestrator.load_sessions().sessions == []
    assert list(worktree_root.glob("*")) == []


def test_submit_dispatch_failure_rolls_back_created_session(tmp_path) -> None:
    worker = FakeWorkerAdapter()
    worker.fail_next_submit("worker submit blew up")
    orchestrator = Orchestrator(
        data_dir=str(tmp_path / ".leopard-gecko"),
        worker=worker,
        task_note_port=FakeTaskNotePort(),
        router=FakeRouter(),
    )

    with pytest.raises(RuntimeError, match="worker submit blew up"):
        orchestrator.submit("add admin users pagination")

    sessions_state = orchestrator.load_sessions()
    task_id = next(iter(orchestrator.paths.tasks_dir.glob("task_*.json"))).stem
    task = orchestrator.task_repo.load(task_id)
    failure_event = _latest_event(orchestrator, "task_dispatch_failed", task_id=task_id)

    assert sessions_state.sessions == []
    assert sessions_state.global_queue == [task_id]
    assert task.queue_status is QueueStatus.QUEUED_GLOBALLY
    assert failure_event.payload["session_id"].startswith("sess_")
    assert failure_event.payload["source"] == "direct"
    assert failure_event.payload["created_session"] is True
    assert failure_event.payload["error"] == "worker submit blew up"


def test_submit_dispatch_failure_restores_existing_idle_session(tmp_path) -> None:
    worker = FakeWorkerAdapter()
    task_note_port = FakeTaskNotePort()
    orchestrator = Orchestrator(
        data_dir=str(tmp_path / ".leopard-gecko"),
        worker=worker,
        task_note_port=task_note_port,
        router=ScenarioRouter(),
    )

    first = orchestrator.submit("add admin users pagination")
    worker.set_poll_state(
        worker.submissions[0].run_id,
        is_running=False,
        exit_code=0,
        worker_context_id="ctx_done",
        last_message="done",
    )
    orchestrator.poll_runs()

    worker.fail_next_submit("idle session submit failed")
    with pytest.raises(RuntimeError, match="idle session submit failed"):
        orchestrator.submit("also fix admin users pagination button style")

    sessions_state = orchestrator.load_sessions()
    session = sessions_state.sessions[0]
    queued_task_id = next(task_id for task_id in _task_ids(orchestrator) if task_id != first.task_id)
    task = orchestrator.task_repo.load(queued_task_id)
    failure_event = _latest_event(orchestrator, "task_dispatch_failed", task_id=queued_task_id)

    assert session.session_id == first.assigned_session_id
    assert session.status is SessionStatus.IDLE
    assert session.current_task_id is None
    assert session.active_run_id is None
    assert session.active_pid is None
    assert session.queue == []
    assert queued_task_id not in [entry.task_id for entry in session.task_history]
    assert sessions_state.global_queue == [queued_task_id]
    assert task.queue_status is QueueStatus.QUEUED_GLOBALLY
    assert failure_event.payload["source"] == "direct"
    assert failure_event.payload["created_session"] is False


def test_poll_completion_dispatches_next_queued_task(tmp_path) -> None:
    worker = FakeWorkerAdapter()
    task_note_port = FakeTaskNotePort()
    orchestrator = Orchestrator(
        data_dir=str(tmp_path / ".leopard-gecko"),
        worker=worker,
        task_note_port=task_note_port,
        router=ScenarioRouter(),
    )

    first = orchestrator.submit("add admin users pagination")
    second = orchestrator.submit("also fix admin users pagination button style")
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
    assert _snapshot_status(orchestrator, first.task_id) is QueueStatus.COMPLETED
    assert _snapshot_status(orchestrator, second.task_id) is QueueStatus.RUNNING
    assert orchestrator.task_repo.load(first.task_id).queue_status is QueueStatus.COMPLETED
    assert orchestrator.task_repo.load(second.task_id).queue_status is QueueStatus.RUNNING


def test_codex_completion_becomes_idle_immediately(tmp_path) -> None:
    worker = FakeWorkerAdapter()
    orchestrator = Orchestrator(
        data_dir=str(tmp_path / ".leopard-gecko"),
        worker=worker,
        task_note_port=FakeTaskNotePort(),
        router=ScenarioRouter(),
    )
    config = orchestrator.init_storage().model_copy(
        update={
            "worker": AppConfig.default().worker.model_copy(update={"backend": WorkerBackend.CODEX})
        }
    )
    orchestrator.config_repo.save(config)

    first = orchestrator.submit("add admin users pagination")
    worker.set_poll_state(
        worker.submissions[0].run_id,
        is_running=False,
        exit_code=0,
        worker_context_id="ctx_done",
        last_message="done",
    )

    poll_result = orchestrator.poll_runs()
    session = orchestrator.load_sessions().sessions[0]

    assert poll_result.completed == 1
    assert poll_result.dispatched == 0
    assert session.status is SessionStatus.IDLE
    assert session.current_task_id is None
    assert orchestrator.task_repo.load(first.task_id).queue_status is QueueStatus.COMPLETED


def test_submit_after_codex_completion_reuses_idle_session_immediately(tmp_path) -> None:
    worker = FakeWorkerAdapter()
    orchestrator = Orchestrator(
        data_dir=str(tmp_path / ".leopard-gecko"),
        worker=worker,
        task_note_port=FakeTaskNotePort(),
        router=ScenarioRouter(),
    )
    config = orchestrator.init_storage().model_copy(
        update={
            "worker": AppConfig.default().worker.model_copy(update={"backend": WorkerBackend.CODEX})
        }
    )
    orchestrator.config_repo.save(config)

    first = orchestrator.submit("add admin users pagination")
    worker.set_poll_state(
        worker.submissions[0].run_id,
        is_running=False,
        exit_code=0,
        worker_context_id="ctx_done",
        last_message="done",
    )
    orchestrator.poll_runs()

    second = orchestrator.submit("also fix admin users pagination button style")
    session = orchestrator.load_sessions().sessions[0]

    assert second.queue_status is QueueStatus.RUNNING
    assert second.assigned_session_id == first.assigned_session_id
    assert session.status is SessionStatus.BUSY
    assert session.current_task_id == second.task_id
    assert session.queue == []
    assert worker.received_context_ids == [None, "ctx_done"]
    assert orchestrator.task_repo.load(second.task_id).queue_status is QueueStatus.RUNNING


def test_poll_completion_promotes_session_queue_from_task_snapshot_when_log_is_missing(tmp_path) -> None:
    worker = FakeWorkerAdapter()
    orchestrator = Orchestrator(
        data_dir=str(tmp_path / ".leopard-gecko"),
        worker=worker,
        task_note_port=FakeTaskNotePort(),
        router=ScenarioRouter(),
    )

    first = orchestrator.submit("add admin users pagination")
    second = orchestrator.submit("also fix admin users pagination button style")
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

    result = orchestrator.submit("add payments export feature")
    snapshot_path = orchestrator.paths.tasks_dir / f"{result.task_id}.json"
    snapshot_path.unlink()

    restored = orchestrator._load_task(result.task_id)

    assert restored.task_id == result.task_id
    assert restored.user_prompt == "add payments export feature"
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

    first = orchestrator.submit("add admin users pagination")
    second = orchestrator.submit("also fix admin users pagination button style")
    worker.set_poll_state(
        worker.submissions[0].run_id,
        is_running=False,
        exit_code=1,
        worker_context_id="ctx_failed",
        last_message="boom",
    )

    poll_result = orchestrator.poll_runs()
    sessions = orchestrator.load_sessions().sessions
    retried_task = orchestrator.task_repo.load(first.task_id)

    assert poll_result.failed == 1
    assert poll_result.dispatched == 2
    assert len(sessions) == 2
    assert sessions[0].current_task_id == second.task_id
    assert sessions[0].worker_context_id == "ctx_failed"
    assert sessions[1].current_task_id == first.task_id
    assert worker.received_context_ids == [None, "ctx_failed", None]
    assert _history_status(sessions[0], first.task_id) is TaskHistoryStatus.FAILED
    assert _history_status(sessions[0], second.task_id) is TaskHistoryStatus.RUNNING
    assert _history_status(sessions[1], first.task_id) is TaskHistoryStatus.RUNNING
    assert _snapshot_status(orchestrator, first.task_id) is QueueStatus.RUNNING
    assert _snapshot_status(orchestrator, second.task_id) is QueueStatus.RUNNING
    assert retried_task.queue_status is QueueStatus.RUNNING
    assert retried_task.retry_count == 1


def test_poll_unknown_session_failure_clears_stale_worker_context(tmp_path) -> None:
    worker = FakeWorkerAdapter()
    orchestrator = Orchestrator(
        data_dir=str(tmp_path / ".leopard-gecko"),
        worker=worker,
        task_note_port=FakeTaskNotePort(),
        router=ScenarioRouter(),
    )
    config = orchestrator.init_storage().model_copy(update={"max_terminal_num": 1})
    orchestrator.config_repo.save(config)

    first = orchestrator.submit("add admin users pagination")
    worker.set_poll_state(
        worker.submissions[0].run_id,
        is_running=False,
        exit_code=0,
        worker_context_id="ctx_done",
        last_message="done",
    )
    orchestrator.poll_runs()

    second = orchestrator.submit("also fix admin users pagination button style")
    worker.set_poll_state(
        worker.submissions[1].run_id,
        is_running=False,
        exit_code=1,
        last_message="Unknown session_id: ctx_done",
    )

    poll_result = orchestrator.poll_runs()
    session = orchestrator.load_sessions().sessions[0]
    retried_task = orchestrator.task_repo.load(second.task_id)

    assert poll_result.failed == 1
    assert poll_result.dispatched == 1
    assert session.session_id == first.assigned_session_id
    assert session.status is SessionStatus.BUSY
    assert session.current_task_id == second.task_id
    assert retried_task.queue_status is QueueStatus.RUNNING
    assert retried_task.retry_count == 1
    assert worker.received_context_ids == [None, "ctx_done", None]


def test_poll_failure_stops_retrying_after_max_retries(tmp_path) -> None:
    worker = FakeWorkerAdapter()
    orchestrator = Orchestrator(
        data_dir=str(tmp_path / ".leopard-gecko"),
        worker=worker,
        task_note_port=FakeTaskNotePort(),
        router=FakeRouter(),
    )
    config = orchestrator.init_storage().model_copy(update={"max_terminal_num": 1})
    orchestrator.config_repo.save(config)

    submission = orchestrator.submit("keep retrying until the limit")

    for expected_retry in (1, 2, 3):
        worker.set_poll_state(
            worker.submissions[-1].run_id,
            is_running=False,
            exit_code=1,
            last_message=f"boom-{expected_retry}",
        )
        poll_result = orchestrator.poll_runs()
        task = orchestrator.task_repo.load(submission.task_id)

        assert poll_result.failed == 1
        assert poll_result.dispatched == 1
        assert task.queue_status is QueueStatus.RUNNING
        assert task.retry_count == expected_retry

    worker.set_poll_state(
        worker.submissions[-1].run_id,
        is_running=False,
        exit_code=1,
        last_message="boom-final",
    )
    poll_result = orchestrator.poll_runs()
    session = orchestrator.load_sessions().sessions[0]
    task = orchestrator.task_repo.load(submission.task_id)

    assert poll_result.failed == 1
    assert poll_result.dispatched == 0
    assert task.queue_status is QueueStatus.FAILED
    assert task.retry_count == 3
    assert session.status is SessionStatus.IDLE
    assert session.current_task_id is None


def test_poll_completion_rolls_back_failed_session_queue_dispatch(tmp_path) -> None:
    worker = FakeWorkerAdapter()
    task_note_port = FakeTaskNotePort()
    orchestrator = Orchestrator(
        data_dir=str(tmp_path / ".leopard-gecko"),
        worker=worker,
        task_note_port=task_note_port,
        router=ScenarioRouter(),
    )

    first = orchestrator.submit("add admin users pagination")
    second = orchestrator.submit("also fix admin users pagination button style")
    worker.set_poll_state(
        worker.submissions[0].run_id,
        is_running=False,
        exit_code=0,
        worker_context_id="ctx_done",
        last_message="done",
    )
    worker.fail_next_submit("session promotion submit failed")

    poll_result = orchestrator.poll_runs()
    session = orchestrator.load_sessions().sessions[0]
    failure_event = _latest_event(orchestrator, "task_dispatch_failed", task_id=second.task_id)

    assert poll_result.completed == 1
    assert poll_result.dispatched == 0
    assert session.status is SessionStatus.IDLE
    assert session.current_task_id is None
    assert session.queue == []
    assert second.task_id not in [entry.task_id for entry in session.task_history]
    assert orchestrator.load_sessions().global_queue == [second.task_id]
    assert orchestrator.task_repo.load(first.task_id).queue_status is QueueStatus.COMPLETED
    assert orchestrator.task_repo.load(second.task_id).queue_status is QueueStatus.QUEUED_GLOBALLY
    assert failure_event.payload["source"] == "session"
    assert failure_event.payload["created_session"] is False


def test_poll_running_updates_worker_context(tmp_path) -> None:
    worker = FakeWorkerAdapter()
    task_note_port = FakeTaskNotePort()
    orchestrator = Orchestrator(
        data_dir=str(tmp_path / ".leopard-gecko"),
        worker=worker,
        task_note_port=task_note_port,
        router=ScenarioRouter(),
    )

    first = orchestrator.submit("add admin users pagination")
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


def test_poll_running_does_not_append_heartbeat_events(tmp_path) -> None:
    worker = FakeWorkerAdapter()
    orchestrator = Orchestrator(
        data_dir=str(tmp_path / ".leopard-gecko"),
        worker=worker,
        task_note_port=FakeTaskNotePort(),
        router=ScenarioRouter(),
    )

    orchestrator.submit("add admin users pagination")
    worker.set_poll_state(
        worker.submissions[0].run_id,
        is_running=True,
        worker_context_id="ctx_running",
    )

    poll_result = orchestrator.poll_runs()
    events = orchestrator.tasks_log.read_all()

    assert poll_result.running == 1
    assert all(event.event_type != "session_heartbeat" for event in events)


def test_poll_running_batch_writes_sessions_once_for_active_runs(tmp_path, monkeypatch) -> None:
    worker = FakeWorkerAdapter()
    orchestrator = Orchestrator(
        data_dir=str(tmp_path / ".leopard-gecko"),
        worker=worker,
        task_note_port=FakeTaskNotePort(),
        router=FakeRouter(),
    )

    for prompt in (
        "add admin users pagination",
        "add payments export feature",
        "fix dashboard filter",
    ):
        orchestrator.submit(prompt)

    for submission in worker.submissions:
        worker.set_poll_state(
            submission.run_id,
            is_running=True,
            worker_context_id=f"ctx::{submission.run_id}",
        )

    write_calls = 0
    original_atomic_write = orchestrator.sessions_repo._atomic_write

    def count_atomic_write(path, payload) -> None:
        nonlocal write_calls
        write_calls += 1
        original_atomic_write(path, payload)

    monkeypatch.setattr(orchestrator.sessions_repo, "_atomic_write", count_atomic_write)

    poll_result = orchestrator.poll_runs()

    assert poll_result.running == 3
    assert write_calls == 2


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

    orchestrator.submit("add admin users pagination")
    second = orchestrator.submit("add payments export feature")
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
    assert _snapshot_status(orchestrator, second.task_id) is QueueStatus.RUNNING
    assert orchestrator.task_repo.load(second.task_id).queue_status is QueueStatus.RUNNING


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

    orchestrator.submit("add admin users pagination")
    second = orchestrator.submit("add payments export feature")

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

    first = orchestrator.submit("add admin users pagination")
    second = orchestrator.submit("add payments export feature")

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
    assert _snapshot_status(orchestrator, second.task_id) is QueueStatus.RUNNING
    assert promotion_event.payload == {
        "session_id": new_session.session_id,
        "source": "global",
        "created_session": True,
    }


def test_submit_expires_stale_idle_session_before_routing(tmp_path) -> None:
    worker = FakeWorkerAdapter()
    orchestrator = Orchestrator(
        data_dir=str(tmp_path / ".leopard-gecko"),
        worker=worker,
        task_note_port=FakeTaskNotePort(),
        router=ScenarioRouter(),
    )
    config = AppConfig.default(str(tmp_path / ".leopard-gecko")).model_copy(update={"max_terminal_num": 1})
    orchestrator.config_repo.save(config)

    def seed_stale_idle(state) -> None:
        state.sessions.append(
            Session(
                session_id="sess_stale_idle",
                status=SessionStatus.IDLE,
                last_heartbeat=_stale_heartbeat(config.session_idle_timeout_min),
            )
        )

    orchestrator.sessions_repo.update(seed_stale_idle)

    result = orchestrator.submit("verify stale idle session cleanup")
    sessions_state = orchestrator.load_sessions()
    stale_session = sessions_state.sessions[0]
    new_session = sessions_state.sessions[1]
    expire_event = next(
        event
        for event in reversed(orchestrator.tasks_log.read_all())
        if event.event_type == "session_expired"
    )

    assert result.routing_decision is RoutingDecision.CREATED_NEW_SESSION
    assert result.assigned_session_id == new_session.session_id
    assert stale_session.session_id == "sess_stale_idle"
    assert stale_session.status is SessionStatus.DEAD
    assert new_session.current_task_id == result.task_id
    assert expire_event.payload == {
        "session_id": "sess_stale_idle",
        "previous_status": SessionStatus.IDLE.value,
        "reason": "stale_timeout",
        "task_ids": [],
    }


def test_poll_expires_stale_blocked_session_and_requeues_current_task(tmp_path) -> None:
    worker = FakeWorkerAdapter()
    orchestrator = Orchestrator(
        data_dir=str(tmp_path / ".leopard-gecko"),
        worker=worker,
        task_note_port=FakeTaskNotePort(),
        router=ScenarioRouter(),
    )
    config = AppConfig.default(str(tmp_path / ".leopard-gecko")).model_copy(update={"max_terminal_num": 1})
    orchestrator.config_repo.save(config)

    submission = orchestrator.submit("stale blocked session after manual recovery")

    def mark_blocked_and_stale(state) -> None:
        session = state.sessions[0]
        session.status = SessionStatus.BLOCKED
        session.last_heartbeat = _stale_heartbeat(config.session_idle_timeout_min)
        session.active_run_id = None
        session.active_pid = None
        session.active_run_started_at = None
        session.last_run_output_path = None

    orchestrator.sessions_repo.update(mark_blocked_and_stale)

    poll_result = orchestrator.poll_runs()
    sessions_state = orchestrator.load_sessions()
    session = sessions_state.sessions[0]
    replacement_session = sessions_state.sessions[1]
    requeue_event = next(
        event
        for event in reversed(orchestrator.tasks_log.read_all())
        if event.event_type == "task_requeued_from_dead_session"
    )
    task = orchestrator.task_repo.load(submission.task_id)

    assert poll_result.running == 0
    assert poll_result.completed == 0
    assert poll_result.failed == 0
    assert poll_result.dispatched == 1
    assert session.status is SessionStatus.DEAD
    assert session.current_task_id is None
    assert session.active_run_id is None
    assert session.active_pid is None
    assert replacement_session.current_task_id == submission.task_id
    assert sessions_state.global_queue == []
    assert task.queue_status is QueueStatus.RUNNING
    assert _history_status(session, submission.task_id) is TaskHistoryStatus.INTERRUPTED
    assert requeue_event.payload == {
        "session_id": session.session_id,
        "previous_status": SessionStatus.BLOCKED.value,
        "reason": "stale_timeout",
    }


def test_submit_expires_stale_busy_session_without_active_run_and_requeues_all_work(tmp_path) -> None:
    worker = FakeWorkerAdapter()
    orchestrator = Orchestrator(
        data_dir=str(tmp_path / ".leopard-gecko"),
        worker=worker,
        task_note_port=FakeTaskNotePort(),
        router=ScenarioRouter(),
    )

    first = orchestrator.submit("add admin users pagination")
    second = orchestrator.submit("also fix admin users pagination button style")

    def break_busy_session(state) -> None:
        session = state.sessions[0]
        session.last_heartbeat = _stale_heartbeat(30)
        session.active_run_id = None
        session.active_pid = None
        session.active_run_started_at = None
        session.last_run_output_path = None

    orchestrator.sessions_repo.update(break_busy_session)

    third = orchestrator.submit("add payments export feature")
    sessions_state = orchestrator.load_sessions()
    session = sessions_state.sessions[0]
    replacement_session = sessions_state.sessions[1]

    assert session.status is SessionStatus.DEAD
    assert session.current_task_id is None
    assert session.queue == []
    assert session.active_run_id is None
    assert session.active_pid is None
    assert third.routing_decision is RoutingDecision.CREATED_NEW_SESSION
    assert replacement_session.current_task_id == third.task_id
    assert sessions_state.global_queue[:2] == [first.task_id, second.task_id]
    assert orchestrator.task_repo.load(first.task_id).queue_status is QueueStatus.QUEUED_GLOBALLY
    assert orchestrator.task_repo.load(second.task_id).queue_status is QueueStatus.QUEUED_GLOBALLY
    assert _history_status(session, first.task_id) is TaskHistoryStatus.INTERRUPTED
    assert _history_status(session, second.task_id) is TaskHistoryStatus.QUEUED


def test_poll_recovers_capacity_after_expiring_stale_blocked_session(tmp_path) -> None:
    worker = FakeWorkerAdapter()
    orchestrator = Orchestrator(
        data_dir=str(tmp_path / ".leopard-gecko"),
        worker=worker,
        task_note_port=FakeTaskNotePort(),
        router=ScenarioRouter(),
    )
    config = AppConfig.default(str(tmp_path / ".leopard-gecko")).model_copy(update={"max_terminal_num": 1})
    orchestrator.config_repo.save(config)

    queued_task_id = "task_waiting_global"
    orchestrator.task_repo.save(
        Task(
            task_id=queued_task_id,
            user_prompt="global queue waiting task",
            task_note="n::global queue waiting task",
            queue_status=QueueStatus.QUEUED_GLOBALLY,
        )
    )

    def seed_state(state) -> None:
        state.sessions.append(
            Session(
                session_id="sess_stale_blocked",
                status=SessionStatus.BLOCKED,
                last_heartbeat=_stale_heartbeat(config.session_idle_timeout_min),
            )
        )
        state.global_queue.append(queued_task_id)

    orchestrator.sessions_repo.update(seed_state)

    poll_result = orchestrator.poll_runs()
    sessions_state = orchestrator.load_sessions()
    stale_session = sessions_state.sessions[0]
    new_session = sessions_state.sessions[1]

    assert poll_result.dispatched == 1
    assert stale_session.status is SessionStatus.DEAD
    assert new_session.current_task_id == queued_task_id
    assert sessions_state.global_queue == []
    assert orchestrator.task_repo.load(queued_task_id).queue_status is QueueStatus.RUNNING
    assert worker.submissions[-1].task_id == queued_task_id


def test_poll_runs_auto_promotes_global_queue_with_no_active_runs_into_idle_session(tmp_path) -> None:
    worker = FakeWorkerAdapter()
    orchestrator = Orchestrator(
        data_dir=str(tmp_path / ".leopard-gecko"),
        worker=worker,
        task_note_port=FakeTaskNotePort(),
        router=ScenarioRouter(),
    )
    config = AppConfig.default(str(tmp_path / ".leopard-gecko")).model_copy(update={"max_terminal_num": 1})
    orchestrator.config_repo.save(config)

    first = orchestrator.submit("add admin users pagination")
    second = orchestrator.submit("add payments export feature")

    def make_idle(state) -> None:
        session = state.sessions[0]
        session.status = SessionStatus.IDLE
        session.current_task_id = None
        session.active_run_id = None
        session.active_pid = None
        session.active_run_started_at = None
        session.last_run_output_path = None

    orchestrator.sessions_repo.update(make_idle)

    poll_result = orchestrator.poll_runs()
    session = orchestrator.load_sessions().sessions[0]

    assert first.task_id != second.task_id
    assert poll_result.running == 0
    assert poll_result.completed == 0
    assert poll_result.failed == 0
    assert poll_result.dispatched == 1
    assert session.status is SessionStatus.BUSY
    assert session.current_task_id == second.task_id
    assert orchestrator.load_sessions().global_queue == []
    assert worker.submissions[1].task_id == second.task_id


def test_poll_runs_auto_promotes_global_queue_with_no_active_runs_into_new_session(tmp_path) -> None:
    worker = FakeWorkerAdapter()
    orchestrator = Orchestrator(
        data_dir=str(tmp_path / ".leopard-gecko"),
        worker=worker,
        task_note_port=FakeTaskNotePort(),
        router=ScenarioRouter(),
    )
    initial_config = AppConfig.default(str(tmp_path / ".leopard-gecko")).model_copy(update={"max_terminal_num": 1})
    orchestrator.config_repo.save(initial_config)

    orchestrator.submit("add admin users pagination")
    second = orchestrator.submit("add payments export feature")
    next_config = initial_config.model_copy(update={"max_terminal_num": 2})
    orchestrator.config_repo.save(next_config)

    def make_dead(state) -> None:
        session = state.sessions[0]
        session.status = SessionStatus.DEAD
        session.current_task_id = None
        session.active_run_id = None
        session.active_pid = None
        session.active_run_started_at = None
        session.last_run_output_path = None

    orchestrator.sessions_repo.update(make_dead)

    poll_result = orchestrator.poll_runs()
    sessions_state = orchestrator.load_sessions()

    assert poll_result.dispatched == 1
    assert len(sessions_state.sessions) == 2
    assert sessions_state.sessions[0].status is SessionStatus.DEAD
    assert sessions_state.sessions[1].status is SessionStatus.BUSY
    assert sessions_state.sessions[1].current_task_id == second.task_id
    assert sessions_state.global_queue == []
    assert worker.submissions[1].task_id == second.task_id


def test_poll_runs_leaves_global_queue_waiting_when_no_active_runs_and_capacity_is_full(tmp_path) -> None:
    worker = FakeWorkerAdapter()
    orchestrator = Orchestrator(
        data_dir=str(tmp_path / ".leopard-gecko"),
        worker=worker,
        task_note_port=FakeTaskNotePort(),
        router=ScenarioRouter(),
    )
    config = AppConfig.default(str(tmp_path / ".leopard-gecko")).model_copy(update={"max_terminal_num": 1})
    orchestrator.config_repo.save(config)

    first = orchestrator.submit("add admin users pagination")
    second = orchestrator.submit("add payments export feature")

    def make_blocked(state) -> None:
        session = state.sessions[0]
        session.status = SessionStatus.BLOCKED
        session.current_task_id = first.task_id
        session.active_run_id = None
        session.active_pid = None
        session.active_run_started_at = None
        session.last_run_output_path = None

    orchestrator.sessions_repo.update(make_blocked)

    poll_result = orchestrator.poll_runs()
    sessions_state = orchestrator.load_sessions()

    assert poll_result.dispatched == 0
    assert len(worker.submissions) == 1
    assert sessions_state.sessions[0].status is SessionStatus.BLOCKED
    assert sessions_state.global_queue == [second.task_id]


def test_poll_runs_bulk_promotes_global_queue_up_to_idle_and_remaining_capacity(tmp_path) -> None:
    worker = FakeWorkerAdapter()
    orchestrator = Orchestrator(
        data_dir=str(tmp_path / ".leopard-gecko"),
        worker=worker,
        task_note_port=FakeTaskNotePort(),
        router=ScenarioRouter(),
    )
    initial_config = AppConfig.default(str(tmp_path / ".leopard-gecko")).model_copy(update={"max_terminal_num": 1})
    orchestrator.config_repo.save(initial_config)

    orchestrator.submit("add admin users pagination")
    second = orchestrator.submit("add payments export feature")
    third = orchestrator.submit("add analytics export feature")
    next_config = initial_config.model_copy(update={"max_terminal_num": 2})
    orchestrator.config_repo.save(next_config)

    def make_idle(state) -> None:
        session = state.sessions[0]
        session.status = SessionStatus.IDLE
        session.current_task_id = None
        session.active_run_id = None
        session.active_pid = None
        session.active_run_started_at = None
        session.last_run_output_path = None

    orchestrator.sessions_repo.update(make_idle)

    poll_result = orchestrator.poll_runs()
    sessions_state = orchestrator.load_sessions()

    assert poll_result.dispatched == 2
    assert len(worker.submissions) == 3
    assert worker.submissions[1].task_id == second.task_id
    assert worker.submissions[2].task_id == third.task_id
    assert sessions_state.global_queue == []
    assert len(sessions_state.sessions) == 2
    assert sessions_state.sessions[0].current_task_id == second.task_id
    assert sessions_state.sessions[1].current_task_id == third.task_id


def test_poll_runs_stops_bulk_global_promotion_after_first_dispatch_failure(tmp_path) -> None:
    worker = SelectiveFailWorkerAdapter()
    orchestrator = Orchestrator(
        data_dir=str(tmp_path / ".leopard-gecko"),
        worker=worker,
        task_note_port=FakeTaskNotePort(),
        router=ScenarioRouter(),
    )
    initial_config = AppConfig.default(str(tmp_path / ".leopard-gecko")).model_copy(update={"max_terminal_num": 1})
    orchestrator.config_repo.save(initial_config)

    orchestrator.submit("add admin users pagination")
    second = orchestrator.submit("add payments export feature")
    third = orchestrator.submit("add analytics export feature")
    next_config = initial_config.model_copy(update={"max_terminal_num": 2})
    orchestrator.config_repo.save(next_config)
    worker.fail_task_ids.add(second.task_id)

    def make_idle(state) -> None:
        session = state.sessions[0]
        session.status = SessionStatus.IDLE
        session.current_task_id = None
        session.active_run_id = None
        session.active_pid = None
        session.active_run_started_at = None
        session.last_run_output_path = None

    orchestrator.sessions_repo.update(make_idle)

    poll_result = orchestrator.poll_runs()
    sessions_state = orchestrator.load_sessions()

    assert poll_result.dispatched == 0
    assert len(worker.submissions) == 1
    assert sessions_state.sessions[0].status is SessionStatus.IDLE
    assert sessions_state.sessions[0].current_task_id is None
    assert sessions_state.global_queue == [second.task_id, third.task_id]


def test_global_queue_dispatch_failure_restores_front_of_queue(tmp_path) -> None:
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

    orchestrator.submit("add admin users pagination")
    second = orchestrator.submit("add payments export feature")
    worker.set_poll_state(
        worker.submissions[0].run_id,
        is_running=False,
        exit_code=0,
        worker_context_id="ctx_done",
        last_message="done",
    )
    worker.fail_next_submit("global promotion submit failed")

    poll_result = orchestrator.poll_runs()
    session = orchestrator.load_sessions().sessions[0]
    failure_event = _latest_event(orchestrator, "task_dispatch_failed", task_id=second.task_id)

    assert poll_result.completed == 1
    assert poll_result.dispatched == 0
    assert session.status is SessionStatus.IDLE
    assert session.current_task_id is None
    assert second.task_id not in [entry.task_id for entry in session.task_history]
    assert orchestrator.load_sessions().global_queue == [second.task_id]
    assert orchestrator.task_repo.load(second.task_id).queue_status is QueueStatus.QUEUED_GLOBALLY
    assert failure_event.payload["source"] == "global"
    assert failure_event.payload["created_session"] is False


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
        orchestrator.submit("add admin users pagination")

    restored = orchestrator.task_repo.load(task_id)
    assert restored.task_id == task_id
    assert restored.user_prompt == "add admin users pagination"
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

    first = orchestrator.submit("add admin users pagination")
    second = orchestrator.submit("also fix admin users pagination button style")
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


def test_submit_and_poll_runs_are_serialized_per_orchestrator(tmp_path) -> None:
    worker = FakeWorkerAdapter()
    task_note_port = BlockingTaskNotePort()
    orchestrator = Orchestrator(
        data_dir=str(tmp_path / ".leopard-gecko"),
        worker=worker,
        task_note_port=task_note_port,
        router=FakeRouter(),
    )

    submit_result: dict[str, object] = {}
    poll_result: dict[str, object] = {}

    submit_thread = threading.Thread(
        target=lambda: submit_result.setdefault(
            "result",
            orchestrator.submit("add admin users pagination"),
        )
    )
    submit_thread.start()
    assert task_note_port.entered.wait(timeout=5)

    poll_thread = threading.Thread(
        target=lambda: poll_result.setdefault("result", orchestrator.poll_runs())
    )
    poll_thread.start()

    time.sleep(0.1)
    assert "result" not in poll_result

    task_note_port.release.set()
    submit_thread.join(timeout=5)
    poll_thread.join(timeout=5)

    assert not submit_thread.is_alive()
    assert not poll_thread.is_alive()
    assert submit_result["result"].queue_status is QueueStatus.RUNNING
    assert poll_result["result"].running == 1
    assert len(worker.submissions) == 1


def test_concurrent_follow_up_submit_and_poll_do_not_drop_session_queue_task(tmp_path) -> None:
    worker = FakeWorkerAdapter()
    router = BlockingSecondDecisionRouter()
    orchestrator = Orchestrator(
        data_dir=str(tmp_path / ".leopard-gecko"),
        worker=worker,
        task_note_port=FakeTaskNotePort(),
        router=router,
    )

    first = orchestrator.submit("add admin users pagination")
    worker.set_poll_state(
        worker.submissions[0].run_id,
        is_running=False,
        exit_code=0,
        worker_context_id="ctx_done",
        last_message="done",
    )

    submit_result: dict[str, object] = {}
    poll_result: dict[str, object] = {}

    submit_thread = threading.Thread(
        target=lambda: submit_result.setdefault(
            "result",
            orchestrator.submit("also fix admin users pagination button style"),
        )
    )
    submit_thread.start()
    assert router.entered.wait(timeout=5)

    poll_thread = threading.Thread(
        target=lambda: poll_result.setdefault("result", orchestrator.poll_runs())
    )
    poll_thread.start()

    time.sleep(0.1)
    assert "result" not in poll_result

    router.release.set()
    submit_thread.join(timeout=5)
    poll_thread.join(timeout=5)

    assert not submit_thread.is_alive()
    assert not poll_thread.is_alive()

    second = submit_result["result"]
    final_state = orchestrator.load_sessions()
    session = final_state.sessions[0]
    second_task = orchestrator.task_repo.load(second.task_id)

    assert second.queue_status is QueueStatus.QUEUED_IN_SESSION
    assert second.assigned_session_id == first.assigned_session_id
    assert poll_result["result"].completed == 1
    assert poll_result["result"].dispatched == 1
    assert session.current_task_id == second.task_id
    assert session.queue == []
    assert _history_status(session, first.task_id) is TaskHistoryStatus.COMPLETED
    assert _history_status(session, second.task_id) is TaskHistoryStatus.RUNNING
    assert second_task.queue_status is QueueStatus.RUNNING
    assert len(worker.submissions) == 2
    assert worker.submissions[1].task_id == second.task_id
    assert worker.received_context_ids == [None, "ctx_done"]
    assert not any(
        event.event_type == "task_requeued_from_orphaned_session_queue"
        and event.task_id == second.task_id
        for event in orchestrator.tasks_log.read_all()
    )


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
        orchestrator.submit("add admin users pagination")


def test_submit_raises_when_default_task_note_generator_cannot_run(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    worker = FakeWorkerAdapter()
    orchestrator = Orchestrator(
        data_dir=str(tmp_path / ".leopard-gecko"),
        worker=worker,
    )

    with pytest.raises(RoutingError, match="Missing OpenAI API key"):
        orchestrator.submit("add admin users pagination")


def test_poll_requeues_orphaned_session_queue_task_and_dispatches_it(tmp_path) -> None:
    worker = FakeWorkerAdapter()
    orchestrator = Orchestrator(
        data_dir=str(tmp_path / ".leopard-gecko"),
        worker=worker,
        task_note_port=FakeTaskNotePort(),
        router=ScenarioRouter(),
    )
    orchestrator.init_storage()
    now = datetime.now(timezone.utc)

    orchestrator.sessions_repo.save(
        SessionsState(
            sessions=[
                Session(
                    session_id="sess_reuse",
                    status=SessionStatus.IDLE,
                    turn_count=1,
                    created_at=now,
                    last_heartbeat=now,
                )
            ],
            global_queue=[],
        )
    )
    orchestrator.task_repo.save(
        Task(
            task_id="task_orphaned",
            user_prompt="also fix admin users pagination button style",
            task_note="n::also fix admin users pagination button style",
            routing=TaskRouting(
                assigned_session_id="sess_reuse",
                decision=RoutingDecision.ASSIGNED_EXISTING,
                reason="matched_for_test score=3",
            ),
            queue_status=QueueStatus.QUEUED_IN_SESSION,
            created_at=now,
        )
    )

    poll_result = orchestrator.poll_runs()
    session = orchestrator.load_sessions().sessions[0]
    task = orchestrator.task_repo.load("task_orphaned")
    recovery_event = _latest_event(
        orchestrator,
        "task_requeued_from_orphaned_session_queue",
        task_id="task_orphaned",
    )

    assert poll_result.dispatched == 1
    assert session.session_id == "sess_reuse"
    assert session.current_task_id == "task_orphaned"
    assert session.queue == []
    assert session.turn_count == 1
    assert _history_status(session, "task_orphaned") is TaskHistoryStatus.RUNNING
    assert task.queue_status is QueueStatus.RUNNING
    assert recovery_event.payload["session_id"] == "sess_reuse"
    assert recovery_event.payload["reason"] == "missing_session_queue"
    assert worker.submissions[0].task_id == "task_orphaned"


def _history_status(session, task_id: str) -> TaskHistoryStatus:
    for entry in session.task_history:
        if entry.task_id == task_id:
            return entry.status
    raise AssertionError(f"missing history for {task_id}")


def _init_git_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.name", "Test User")
    _git(path, "config", "user.email", "test@example.com")
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    _git(path, "add", "README.md")
    _git(path, "commit", "-m", "init")
    return path


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _snapshot_status(orchestrator: Orchestrator, task_id: str) -> QueueStatus:
    return orchestrator.task_repo.load(task_id).queue_status


def _tokens(text: str) -> set[str]:
    return {token.lower() for token in re.findall(r"[\w-]{2,}", text)}


def _stale_heartbeat(timeout_min: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(minutes=timeout_min + 1)


def _task_ids(orchestrator: Orchestrator) -> list[str]:
    return sorted(path.stem for path in orchestrator.paths.tasks_dir.glob("task_*.json"))


def _latest_event(orchestrator: Orchestrator, event_type: str, *, task_id: str):
    for event in reversed(orchestrator.tasks_log.read_all()):
        if event.event_type == event_type and event.task_id == task_id:
            return event
    raise AssertionError(f"missing event {event_type} for {task_id}")
