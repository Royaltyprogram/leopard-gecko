from datetime import datetime, timezone
from pathlib import Path
from secrets import token_hex

from pydantic import BaseModel

from leopard_gecko.adapters.base import WorkerPort, WorkerRunState, WorkerSubmission
from leopard_gecko.adapters.factory import build_worker
from leopard_gecko.models.config import AppConfig, WorkerBackend
from leopard_gecko.models.session import (
    Session,
    SessionStatus,
    SessionsState,
    TaskHistoryEntry,
    TaskHistoryStatus,
    live_session_count,
)
from leopard_gecko.models.task import QueueStatus, RoutingDecision, Task, TaskEvent, TaskRouting
from leopard_gecko.router.factory import build_router
from leopard_gecko.router.policy import (
    ContextRouter,
    RouteAction,
    RouteDecision,
    RoutingError,
    SessionSnapshot,
    build_session_snapshots,
)
from leopard_gecko.router.task_notes import AgentTaskNoteGenerator, TaskNotePort
from leopard_gecko.store.config_repo import ConfigRepository
from leopard_gecko.store.paths import DataPaths, resolve_data_paths
from leopard_gecko.store.sessions_repo import SessionsRepository
from leopard_gecko.store.task_repo import TaskRepository
from leopard_gecko.store.tasks_log import TasksLog


class SubmissionResult(BaseModel):
    task_id: str
    queue_status: QueueStatus
    routing_decision: RoutingDecision
    assigned_session_id: str | None = None
    created_session: bool = False
    dispatched: bool = False


class DispatchRequest(BaseModel):
    session_id: str
    task_id: str
    user_prompt: str
    promoted_from_queue: str | None = None
    created_session: bool = False


class SubmissionMutation(BaseModel):
    result: SubmissionResult
    dispatch_request: DispatchRequest | None = None


class ActiveRun(BaseModel):
    session_id: str
    task_id: str
    run_id: str | None = None
    process_id: int | None = None
    output_path: Path | None = None


class TransitionResult(BaseModel):
    next_dispatch: DispatchRequest | None = None
    should_promote_global: bool = False


class PollRunsResult(BaseModel):
    running: int = 0
    completed: int = 0
    failed: int = 0
    dispatched: int = 0


class Orchestrator:
    def __init__(
        self,
        *,
        data_dir: str | None = None,
        cwd: Path | None = None,
        worker: WorkerPort | None = None,
        worker_backend: WorkerBackend | str | None = None,
        task_note_port: TaskNotePort | None = None,
        router: ContextRouter | None = None,
    ) -> None:
        self.cwd = cwd or Path.cwd()
        self.paths: DataPaths = resolve_data_paths(data_dir=data_dir, cwd=self.cwd)
        self.config_repo = ConfigRepository(self.paths)
        self.sessions_repo = SessionsRepository(self.paths)
        self.task_repo = TaskRepository(self.paths)
        self.tasks_log = TasksLog(self.paths)
        self.worker = worker
        self.worker_backend = WorkerBackend(worker_backend) if worker_backend else None
        self.task_note_port = task_note_port
        self.router = router

    def init_storage(self) -> AppConfig:
        config = self.config_repo.initialize()
        self.sessions_repo.initialize()
        self.task_repo.initialize()
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
        task_note_port = self._resolve_task_note_port(config)
        task = Task(
            task_id=_generate_prefixed_id("task"),
            user_prompt=normalized_prompt,
            task_note=task_note_port.make_note(normalized_prompt),
        )
        self.task_repo.save(task)

        self.tasks_log.append(
            TaskEvent(
                event_type="task_created",
                task_id=task.task_id,
                payload=task.model_dump(mode="json"),
            )
        )

        mutation = self.sessions_repo.update(
            lambda state: self._apply_submission(state=state, task=task, config=config)
        )
        self.task_repo.save(task)
        self.tasks_log.append(
            TaskEvent(
                event_type="task_routed",
                task_id=task.task_id,
                payload={
                    "decision": task.routing.decision,
                    "assigned_session_id": mutation.result.assigned_session_id,
                    "queue_status": task.queue_status,
                    "reason": task.routing.reason,
                },
            )
        )

        if mutation.dispatch_request is not None:
            self._dispatch_task(config=config, request=mutation.dispatch_request)

        return mutation.result

    def poll_runs(self) -> PollRunsResult:
        config = self.init_storage()
        worker = self._resolve_worker(config)
        poll_result = PollRunsResult()

        for active_run in self._collect_active_runs():
            run_state = worker.poll(
                run_id=active_run.run_id,
                process_id=active_run.process_id,
                output_path=active_run.output_path,
            )
            if run_state.is_running:
                updated = self.sessions_repo.update(
                    lambda state: self._refresh_running_session(
                        state=state,
                        active_run=active_run,
                        run_state=run_state,
                    )
                )
                if updated:
                    poll_result.running += 1
                    self.tasks_log.append(
                        TaskEvent(
                            event_type="session_heartbeat",
                            task_id=active_run.task_id,
                            payload={
                                "session_id": active_run.session_id,
                                "run_id": active_run.run_id,
                            },
                        )
                    )
                continue

            if run_state.requires_manual_recovery:
                blocked = self.sessions_repo.update(
                    lambda state: self._block_run(
                        state=state,
                        active_run=active_run,
                        run_state=run_state,
                    )
                )
                if blocked:
                    self.tasks_log.append(
                        TaskEvent(
                            event_type="task_blocked",
                            task_id=active_run.task_id,
                            payload={
                                "session_id": active_run.session_id,
                                "run_id": active_run.run_id,
                                "summary": run_state.last_message,
                                "reason": run_state.recovery_reason,
                            },
                        )
                    )
                continue

            transition = self.sessions_repo.update(
                lambda state: self._finalize_run(
                    state=state,
                    active_run=active_run,
                    run_state=run_state,
                )
            )
            event_type = "task_completed" if run_state.exit_code == 0 else "task_failed"
            if run_state.exit_code == 0:
                poll_result.completed += 1
            else:
                poll_result.failed += 1
            self.tasks_log.append(
                TaskEvent(
                    event_type=event_type,
                    task_id=active_run.task_id,
                    payload={
                        "session_id": active_run.session_id,
                        "run_id": active_run.run_id,
                        "exit_code": run_state.exit_code,
                        "summary": run_state.last_message,
                    },
                )
            )

            if transition.next_dispatch is not None:
                self._dispatch_task(config=config, request=transition.next_dispatch)
                poll_result.dispatched += 1
            elif transition.should_promote_global and self._promote_next_global_task(config):
                poll_result.dispatched += 1

        return poll_result

    def _apply_submission(
        self,
        *,
        state: SessionsState,
        task: Task,
        config: AppConfig,
    ) -> SubmissionMutation:
        router = self._resolve_router(config)
        snapshots = build_session_snapshots(
            state.sessions,
            history_limit=router.history_limit,
        )
        route = router.decide(
            task=task,
            config=config,
            sessions=snapshots,
            global_queue_size=len(state.global_queue),
        )
        _validate_route_decision(
            route=route,
            config=config,
            sessions=snapshots,
        )
        now = datetime.now(timezone.utc)

        created_session = False
        dispatch_request: DispatchRequest | None = None
        assigned_session_id: str | None = None

        if route.action is RouteAction.CREATE_NEW_SESSION:
            dispatch_request = self._start_task_in_new_session(
                state=state,
                task=task,
            )
            task.routing = TaskRouting(
                assigned_session_id=dispatch_request.session_id,
                decision=RoutingDecision.CREATED_NEW_SESSION,
                reason=route.reason,
            )
            task.queue_status = QueueStatus.RUNNING
            assigned_session_id = dispatch_request.session_id
            created_session = True
        elif route.action is RouteAction.ASSIGN_EXISTING:
            session = _find_session(state, route.session_id)
            assigned_session_id = session.session_id

            if session.status is SessionStatus.IDLE and session.current_task_id is None:
                dispatch_request = self._start_task_in_existing_idle_session(
                    session=session,
                    task=task,
                )
                task.queue_status = QueueStatus.RUNNING
            else:
                session.queue.append(task.task_id)
                session.task_history.append(
                    TaskHistoryEntry(
                        task_id=task.task_id,
                        user_prompt=task.user_prompt,
                        task_note=task.task_note,
                        status=TaskHistoryStatus.QUEUED,
                        updated_at=now,
                    )
                )
                task.queue_status = QueueStatus.QUEUED_IN_SESSION

            task.routing = TaskRouting(
                assigned_session_id=session.session_id,
                decision=RoutingDecision.ASSIGNED_EXISTING,
                reason=route.reason,
            )
        else:
            state.global_queue.append(task.task_id)
            task.routing = TaskRouting(
                assigned_session_id=None,
                decision=RoutingDecision.ENQUEUED_GLOBAL,
                reason=route.reason,
            )
            task.queue_status = QueueStatus.QUEUED_GLOBALLY

        result = SubmissionResult(
            task_id=task.task_id,
            queue_status=task.queue_status,
            routing_decision=task.routing.decision,
            assigned_session_id=assigned_session_id,
            created_session=created_session,
            dispatched=dispatch_request is not None,
        )
        return SubmissionMutation(
            result=result,
            dispatch_request=dispatch_request,
        )

    def _collect_active_runs(self) -> list[ActiveRun]:
        state = self.sessions_repo.load()
        active_runs: list[ActiveRun] = []

        for session in state.sessions:
            if session.current_task_id is None:
                continue
            if session.active_run_id is None and session.active_pid is None:
                continue
            active_runs.append(
                ActiveRun(
                    session_id=session.session_id,
                    task_id=session.current_task_id,
                    run_id=session.active_run_id,
                    process_id=session.active_pid,
                    output_path=Path(session.last_run_output_path)
                    if session.last_run_output_path
                    else None,
                )
            )

        return active_runs

    def _refresh_running_session(
        self,
        *,
        state: SessionsState,
        active_run: ActiveRun,
        run_state: WorkerRunState,
    ) -> bool:
        session = _find_session(state, active_run.session_id)
        if not _session_matches_run(session, active_run):
            return False

        now = datetime.now(timezone.utc)
        if run_state.worker_context_id:
            session.worker_context_id = run_state.worker_context_id
        session.last_heartbeat = now
        return True

    def _finalize_run(
        self,
        *,
        state: SessionsState,
        active_run: ActiveRun,
        run_state: WorkerRunState,
    ) -> TransitionResult:
        session = _find_session(state, active_run.session_id)
        if not _session_matches_run(session, active_run):
            return TransitionResult()

        if run_state.exit_code == 0:
            return self._complete_running_task(
                session=session,
                task_id=active_run.task_id,
                run_state=run_state,
            )
        return self._fail_running_task(
            session=session,
            task_id=active_run.task_id,
            run_state=run_state,
        )

    def _block_run(
        self,
        *,
        state: SessionsState,
        active_run: ActiveRun,
        run_state: WorkerRunState,
    ) -> bool:
        session = _find_session(state, active_run.session_id)
        if not _session_matches_run(session, active_run):
            return False

        now = datetime.now(timezone.utc)
        history_entry = _find_history_entry(session, active_run.task_id)
        history_entry.status = TaskHistoryStatus.INTERRUPTED
        history_entry.summary = run_state.last_message
        history_entry.updated_at = now

        if run_state.worker_context_id:
            session.worker_context_id = run_state.worker_context_id
        session.status = SessionStatus.BLOCKED
        session.last_heartbeat = now
        _clear_active_run(session)
        return True

    def _complete_running_task(
        self,
        *,
        session: Session,
        task_id: str,
        run_state: WorkerRunState,
    ) -> TransitionResult:
        return self._close_running_task(
            session=session,
            task_id=task_id,
            next_status=TaskHistoryStatus.COMPLETED,
            run_state=run_state,
        )

    def _fail_running_task(
        self,
        *,
        session: Session,
        task_id: str,
        run_state: WorkerRunState,
    ) -> TransitionResult:
        return self._close_running_task(
            session=session,
            task_id=task_id,
            next_status=TaskHistoryStatus.FAILED,
            run_state=run_state,
        )

    def _close_running_task(
        self,
        *,
        session: Session,
        task_id: str,
        next_status: TaskHistoryStatus,
        run_state: WorkerRunState,
    ) -> TransitionResult:
        now = datetime.now(timezone.utc)
        history_entry = _find_history_entry(session, task_id)
        history_entry.status = next_status
        history_entry.summary = run_state.last_message
        history_entry.updated_at = now

        if run_state.worker_context_id:
            session.worker_context_id = run_state.worker_context_id
        session.last_heartbeat = now
        _clear_active_run(session)

        next_dispatch: DispatchRequest | None = None
        if session.current_task_id == task_id:
            session.current_task_id = None
            if session.queue:
                next_task_id = session.queue.pop(0)
                next_task = self._load_task(next_task_id)
                session.current_task_id = next_task.task_id
                session.status = SessionStatus.BUSY
                next_history_entry = _find_history_entry(session, next_task.task_id)
                next_history_entry.status = TaskHistoryStatus.RUNNING
                next_history_entry.updated_at = now
                next_dispatch = DispatchRequest(
                    session_id=session.session_id,
                    task_id=next_task.task_id,
                    user_prompt=next_task.user_prompt,
                    promoted_from_queue="session",
                )
            else:
                session.status = SessionStatus.IDLE

        return TransitionResult(
            next_dispatch=next_dispatch,
            should_promote_global=next_dispatch is None and session.status is SessionStatus.IDLE,
        )

    def _promote_next_global_task(self, config: AppConfig) -> bool:
        request = self.sessions_repo.update(
            lambda state: self._reserve_global_dispatch(state=state, config=config)
        )
        if request is None:
            return False
        self._dispatch_task(config=config, request=request)
        return True

    def _reserve_global_dispatch(
        self,
        *,
        state: SessionsState,
        config: AppConfig,
    ) -> DispatchRequest | None:
        if not state.global_queue:
            return None

        idle_session = next(
            (
                session
                for session in state.sessions
                if session.status is SessionStatus.IDLE and session.current_task_id is None
            ),
            None,
        )
        task_id = state.global_queue[0]
        task = self._load_task(task_id)
        if idle_session is not None:
            state.global_queue.pop(0)
            return self._start_task_in_existing_idle_session(
                session=idle_session,
                task=task,
                promoted_from_queue="global",
            )

        if live_session_count(state.sessions) >= config.max_terminal_num:
            return None

        state.global_queue.pop(0)
        return self._start_task_in_new_session(
            state=state,
            task=task,
            promoted_from_queue="global",
        )

    def _start_task_in_existing_idle_session(
        self,
        *,
        session: Session,
        task: Task,
        promoted_from_queue: str | None = None,
    ) -> DispatchRequest:
        now = datetime.now(timezone.utc)
        session.status = SessionStatus.BUSY
        session.current_task_id = task.task_id
        session.last_heartbeat = now
        session.task_history.append(
            TaskHistoryEntry(
                task_id=task.task_id,
                user_prompt=task.user_prompt,
                task_note=task.task_note,
                status=TaskHistoryStatus.RUNNING,
                updated_at=now,
            )
        )
        return DispatchRequest(
            session_id=session.session_id,
            task_id=task.task_id,
            user_prompt=task.user_prompt,
            promoted_from_queue=promoted_from_queue,
        )

    def _start_task_in_new_session(
        self,
        *,
        state: SessionsState,
        task: Task,
        promoted_from_queue: str | None = None,
    ) -> DispatchRequest:
        now = datetime.now(timezone.utc)
        session = Session(
            session_id=_generate_prefixed_id("sess"),
            status=SessionStatus.BUSY,
            current_task_id=task.task_id,
            last_heartbeat=now,
        )
        session.task_history.append(
            TaskHistoryEntry(
                task_id=task.task_id,
                user_prompt=task.user_prompt,
                task_note=task.task_note,
                status=TaskHistoryStatus.RUNNING,
                updated_at=now,
            )
        )
        state.sessions.append(session)
        return DispatchRequest(
            session_id=session.session_id,
            task_id=task.task_id,
            user_prompt=task.user_prompt,
            promoted_from_queue=promoted_from_queue,
            created_session=True,
        )

    def _resolve_worker(self, config: AppConfig) -> WorkerPort:
        if self.worker is None:
            self.worker = build_worker(config, self.worker_backend)
        return self.worker

    def _resolve_task_note_port(self, config: AppConfig) -> TaskNotePort:
        if self.task_note_port is None:
            self.task_note_port = AgentTaskNoteGenerator(config.router.agent)
        return self.task_note_port

    def _resolve_router(self, config: AppConfig) -> ContextRouter:
        if self.router is None:
            self.router = build_router(config)
        return self.router

    def _dispatch_task(self, *, config: AppConfig, request: DispatchRequest) -> WorkerSubmission:
        worker = self._resolve_worker(config)
        session = _find_session(self.sessions_repo.load(), request.session_id)
        worker_context_id = session.worker_context_id
        backend = session.worker_backend or self._selected_backend(config).value

        submission = worker.submit(
            request.session_id,
            request.task_id,
            request.user_prompt,
            cwd=self.cwd,
            data_dir=self.paths.root_dir,
            worker_context_id=worker_context_id,
        )

        def persist_submission(state: SessionsState) -> None:
            current_session = _find_session(state, request.session_id)
            now = datetime.now(timezone.utc)
            current_session.worker_backend = backend
            if submission.worker_context_id:
                current_session.worker_context_id = submission.worker_context_id
            current_session.active_run_id = submission.run_id
            current_session.active_pid = submission.process_id
            current_session.active_run_started_at = now
            current_session.last_run_output_path = submission.output_path
            current_session.last_heartbeat = now

        self.sessions_repo.update(persist_submission)

        if request.promoted_from_queue:
            payload = {
                "session_id": request.session_id,
                "source": request.promoted_from_queue,
            }
            if request.created_session:
                payload["created_session"] = True
            self.tasks_log.append(
                TaskEvent(
                    event_type="task_promoted_from_queue",
                    task_id=request.task_id,
                    payload=payload,
                )
            )
        self.tasks_log.append(
            TaskEvent(
                event_type="task_dispatched",
                task_id=request.task_id,
                payload={
                    "session_id": request.session_id,
                    "run_id": submission.run_id,
                    "process_id": submission.process_id,
                    "worker_context_id": submission.worker_context_id or worker_context_id,
                    "output_path": submission.output_path,
                },
            )
        )
        return submission

    def _selected_backend(self, config: AppConfig) -> WorkerBackend:
        return self.worker_backend or config.worker.backend

    def _load_task(self, task_id: str) -> Task:
        if self.task_repo.exists(task_id):
            return self.task_repo.load(task_id)

        for event in reversed(self.tasks_log.read_all()):
            if event.event_type != "task_created" or event.task_id != task_id:
                continue
            task = Task.model_validate(event.payload)
            self.task_repo.save(task)
            return task
        raise ValueError(f"Unknown task_id: {task_id}")


def _find_session(state: SessionsState, session_id: str | None) -> Session:
    if session_id is None:
        raise ValueError("session_id is required for an existing-session assignment")
    for session in state.sessions:
        if session.session_id == session_id:
            return session
    raise ValueError(f"Unknown session_id: {session_id}")


def _find_history_entry(session: Session, task_id: str) -> TaskHistoryEntry:
    for entry in reversed(session.task_history):
        if entry.task_id == task_id:
            return entry
    raise ValueError(f"Unknown task_id in session history: {task_id}")


def _session_matches_run(session: Session, active_run: ActiveRun) -> bool:
    if session.current_task_id != active_run.task_id:
        return False
    if active_run.run_id is not None and session.active_run_id != active_run.run_id:
        return False
    if active_run.process_id is not None and session.active_pid != active_run.process_id:
        return False
    return True


def _clear_active_run(session: Session) -> None:
    session.active_run_id = None
    session.active_pid = None
    session.active_run_started_at = None
    session.last_run_output_path = None


def _validate_route_decision(
    *,
    route: RouteDecision,
    config: AppConfig,
    sessions: list[SessionSnapshot],
) -> None:
    live_sessions = live_session_count(sessions)

    if route.action is RouteAction.ASSIGN_EXISTING:
        if not route.session_id:
            raise RoutingError("Router returned assign_existing without a session_id.")
        session = next((session for session in sessions if session.session_id == route.session_id), None)
        if session is None:
            raise RoutingError(f"Router returned unknown session_id: {route.session_id}")
        if session.status is SessionStatus.DEAD:
            raise RoutingError(f"Router returned dead session_id: {route.session_id}")
        if session.queue_size >= config.queue_policy.max_queue_per_session:
            raise RoutingError(f"Router returned full session_id: {route.session_id}")
        return

    if route.session_id:
        raise RoutingError(f"Router returned unexpected session_id for action {route.action}.")

    if route.action is RouteAction.CREATE_NEW_SESSION and live_sessions >= config.max_terminal_num:
        raise RoutingError("Router requested create_new_session but capacity is already exhausted.")

    if route.action is RouteAction.ENQUEUE_GLOBAL and live_sessions < config.max_terminal_num:
        raise RoutingError("Router requested enqueue_global even though new-session capacity is available.")


def _generate_prefixed_id(prefix: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"{prefix}_{timestamp}_{token_hex(2)}"
