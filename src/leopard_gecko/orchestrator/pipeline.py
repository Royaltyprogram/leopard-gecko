from datetime import datetime, timezone
from pathlib import Path
from secrets import token_hex

from pydantic import BaseModel

from leopard_gecko.adapters.base import WorkerPort
from leopard_gecko.adapters.noop import NoopWorkerAdapter
from leopard_gecko.models.config import AppConfig
from leopard_gecko.models.session import (
    Session,
    SessionStatus,
    SessionsState,
    TaskHistoryEntry,
    TaskHistoryStatus,
)
from leopard_gecko.models.task import QueueStatus, RoutingDecision, Task, TaskEvent, TaskRouting
from leopard_gecko.router.policy import RouteAction, decide_route
from leopard_gecko.store.config_repo import ConfigRepository
from leopard_gecko.store.paths import DataPaths, resolve_data_paths
from leopard_gecko.store.sessions_repo import SessionsRepository
from leopard_gecko.store.tasks_log import TasksLog


class SubmissionResult(BaseModel):
    task_id: str
    queue_status: QueueStatus
    routing_decision: RoutingDecision
    assigned_session_id: str | None = None
    created_session: bool = False
    dispatched: bool = False


class Orchestrator:
    def __init__(
        self,
        *,
        data_dir: str | None = None,
        cwd: Path | None = None,
        worker: WorkerPort | None = None,
    ) -> None:
        self.paths: DataPaths = resolve_data_paths(data_dir=data_dir, cwd=cwd)
        self.config_repo = ConfigRepository(self.paths)
        self.sessions_repo = SessionsRepository(self.paths)
        self.tasks_log = TasksLog(self.paths)
        self.worker = worker or NoopWorkerAdapter()

    def init_storage(self) -> AppConfig:
        config = self.config_repo.initialize()
        self.sessions_repo.initialize()
        self.tasks_log.initialize()
        return config

    def load_config(self) -> AppConfig:
        return self.config_repo.load()

    def load_sessions(self) -> SessionsState:
        return self.sessions_repo.load()

    def submit(self, user_prompt: str) -> SubmissionResult:
        normalized_prompt = user_prompt.strip()
        if not normalized_prompt:
            raise ValueError("user_prompt must not be blank")

        config = self.init_storage()
        state = self.sessions_repo.load()
        task = Task(
            task_id=_generate_prefixed_id("task"),
            user_prompt=normalized_prompt,
            task_note=_make_task_note(normalized_prompt),
        )
        route = decide_route(task, config, state)

        created_session = False
        dispatched = False
        assigned_session_id: str | None = None

        if route.action is RouteAction.CREATE_NEW_SESSION:
            session = Session(
                session_id=_generate_prefixed_id("sess"),
                status=SessionStatus.BUSY,
                current_task_id=task.task_id,
            )
            session.task_history.append(
                TaskHistoryEntry(
                    task_id=task.task_id,
                    user_prompt=task.user_prompt,
                    task_note=task.task_note,
                    status=TaskHistoryStatus.RUNNING,
                )
            )
            state.sessions.append(session)
            task.routing = TaskRouting(
                assigned_session_id=session.session_id,
                decision=RoutingDecision.CREATED_NEW_SESSION,
                reason=route.reason,
            )
            task.queue_status = QueueStatus.RUNNING
            assigned_session_id = session.session_id
            created_session = True
            dispatched = True
        elif route.action is RouteAction.ASSIGN_EXISTING:
            session = _find_session(state, route.session_id)
            if session.status is SessionStatus.IDLE and session.current_task_id is None:
                session.status = SessionStatus.BUSY
                session.current_task_id = task.task_id
                session.last_heartbeat = datetime.now(timezone.utc)
                session.task_history.append(
                    TaskHistoryEntry(
                        task_id=task.task_id,
                        user_prompt=task.user_prompt,
                        task_note=task.task_note,
                        status=TaskHistoryStatus.RUNNING,
                    )
                )
                task.queue_status = QueueStatus.RUNNING
                dispatched = True
            else:
                session.queue.append(task.task_id)
                session.task_history.append(
                    TaskHistoryEntry(
                        task_id=task.task_id,
                        user_prompt=task.user_prompt,
                        task_note=task.task_note,
                        status=TaskHistoryStatus.QUEUED,
                    )
                )
                task.queue_status = QueueStatus.QUEUED_IN_SESSION
            task.routing = TaskRouting(
                assigned_session_id=session.session_id,
                decision=RoutingDecision.ASSIGNED_EXISTING,
                reason=route.reason,
            )
            assigned_session_id = session.session_id
        else:
            state.global_queue.append(task.task_id)
            task.routing = TaskRouting(
                assigned_session_id=None,
                decision=RoutingDecision.ENQUEUED_GLOBAL,
                reason=route.reason,
            )
            task.queue_status = QueueStatus.QUEUED_GLOBALLY

        self.tasks_log.append(
            TaskEvent(
                event_type="task_created",
                task_id=task.task_id,
                payload=task.model_dump(mode="json"),
            )
        )
        self.tasks_log.append(
            TaskEvent(
                event_type="task_routed",
                task_id=task.task_id,
                payload={
                    "decision": task.routing.decision,
                    "assigned_session_id": assigned_session_id,
                    "queue_status": task.queue_status,
                    "reason": task.routing.reason,
                },
            )
        )
        self.sessions_repo.save(state)

        if dispatched and assigned_session_id:
            self.worker.submit(assigned_session_id, task.user_prompt)

        return SubmissionResult(
            task_id=task.task_id,
            queue_status=task.queue_status,
            routing_decision=task.routing.decision,
            assigned_session_id=assigned_session_id,
            created_session=created_session,
            dispatched=dispatched,
        )


def _find_session(state: SessionsState, session_id: str | None) -> Session:
    if session_id is None:
        raise ValueError("session_id is required for an existing-session assignment")
    for session in state.sessions:
        if session.session_id == session_id:
            return session
    raise ValueError(f"Unknown session_id: {session_id}")


def _generate_prefixed_id(prefix: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"{prefix}_{timestamp}_{token_hex(2)}"


def _make_task_note(user_prompt: str) -> str:
    preview = " ".join(user_prompt.split())
    shortened = preview[:100].rstrip()
    if len(preview) > 100:
        shortened += "..."
    return f"Routing note: likely related to '{shortened}'."

