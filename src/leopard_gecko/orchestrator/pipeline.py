from datetime import datetime, timedelta, timezone
from pathlib import Path
from secrets import token_hex
from threading import RLock

from pydantic import BaseModel, Field

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
from leopard_gecko.worktree import SessionWorktree, WorktreeManager


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
    original_queue_source: str = "direct"
    promoted_from_queue: str | None = None
    created_session: bool = False


class ExpiredSession(BaseModel):
    session_id: str
    previous_status: SessionStatus
    requeued_task_ids: list[str] = Field(default_factory=list)


class ExpireResult(BaseModel):
    expired_sessions: list[ExpiredSession] = Field(default_factory=list)


class SubmissionMutation(BaseModel):
    result: SubmissionResult
    dispatch_request: DispatchRequest | None = None
    expire_result: ExpireResult = Field(default_factory=ExpireResult)


class ActiveRun(BaseModel):
    session_id: str
    task_id: str
    run_id: str | None = None
    process_id: int | None = None
    output_path: Path | None = None


class TransitionResult(BaseModel):
    next_dispatch: DispatchRequest | None = None


class PollRunsResult(BaseModel):
    running: int = 0
    completed: int = 0
    failed: int = 0
    dispatched: int = 0


class PolledRun(BaseModel):
    active_run: ActiveRun
    run_state: WorkerRunState


class TaskQueueStatusUpdate(BaseModel):
    task_id: str
    queue_status: QueueStatus
    retry_count: int | None = None


class DispatchContext(BaseModel):
    cwd: Path
    worker_context_id: str | None = None
    backend: str
    worktree: SessionWorktree | None = None


class QueuedTaskRecovery(BaseModel):
    task_id: str
    session_id: str | None = None
    reason: str


class PollMutation(BaseModel):
    poll_result: PollRunsResult = Field(default_factory=PollRunsResult)
    dispatch_requests: list[DispatchRequest] = Field(default_factory=list)
    task_events: list[TaskEvent] = Field(default_factory=list)
    task_status_updates: list[TaskQueueStatusUpdate] = Field(default_factory=list)


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
        self._worker_is_external = worker is not None
        self._resolved_worker_backend: WorkerBackend | None = None
        self.worker_backend = WorkerBackend(worker_backend) if worker_backend else None
        self.task_note_port = task_note_port
        self.router = router
        self._operation_lock = RLock()

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
        with self._operation_lock:
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
            self._persist_expire_result(mutation.expire_result)
            self._update_task_snapshot(
                task.task_id,
                queue_status=task.queue_status,
                routing=task.routing,
            )
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
                self._dispatch_with_rollback(
                    config=config,
                    request=mutation.dispatch_request,
                    propagate_error=True,
                )

            return mutation.result

    def poll_runs(self) -> PollRunsResult:
        with self._operation_lock:
            config = self.init_storage()
            self._reconcile_orphaned_running_tasks(config=config)
            worker = self._resolve_worker(config)
            now = datetime.now(timezone.utc)
            expire_result = self._expire_stale_sessions_in_repo(config=config, now=now)
            self._persist_expire_result(expire_result)
            self._reconcile_orphaned_queued_session_tasks()
            snapshot = self.sessions_repo.load_snapshot()
            polled_runs: list[PolledRun] = []

            for active_run in self._collect_active_runs(snapshot.state):
                polled_runs.append(
                    PolledRun(
                        active_run=active_run,
                        run_state=worker.poll(
                            run_id=active_run.run_id,
                            process_id=active_run.process_id,
                            output_path=active_run.output_path,
                        ),
                    )
                )

            mutation = self.sessions_repo.update_from_snapshot(
                snapshot,
                lambda state: self._apply_polled_runs(
                    state=state,
                    config=config,
                    polled_runs=polled_runs,
                ),
            )

            for status_update in mutation.task_status_updates:
                self._update_task_snapshot(
                    status_update.task_id,
                    queue_status=status_update.queue_status,
                    retry_count=status_update.retry_count,
                )

            for event in mutation.task_events:
                self.tasks_log.append(event)

            for index, request in enumerate(mutation.dispatch_requests):
                if self._dispatch_with_rollback(config=config, request=request):
                    mutation.poll_result.dispatched += 1
                    continue
                self._restore_reserved_dispatch_requests(
                    failed_request=request,
                    requests=mutation.dispatch_requests[index + 1 :],
                )
                break

            return mutation.poll_result

    def _apply_submission(
        self,
        *,
        state: SessionsState,
        task: Task,
        config: AppConfig,
    ) -> SubmissionMutation:
        now = datetime.now(timezone.utc)
        expire_result = self._expire_stale_sessions(
            state=state,
            config=config,
            now=now,
        )
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
                session.turn_count += 1
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
            expire_result=expire_result,
        )

    def _expire_stale_sessions(
        self,
        *,
        state: SessionsState,
        config: AppConfig,
        now: datetime,
    ) -> ExpireResult:
        timeout = timedelta(minutes=config.session_idle_timeout_min)
        result = ExpireResult()

        for session in state.sessions:
            if session.status is SessionStatus.DEAD:
                continue
            if now - session.last_heartbeat <= timeout:
                continue
            if session.status is SessionStatus.BUSY and _session_has_active_run(session):
                continue

            previous_status = session.status
            requeued_task_ids = self._requeue_dead_session_tasks(
                session=session,
                state=state,
                now=now,
            )
            session.status = SessionStatus.DEAD
            result.expired_sessions.append(
                ExpiredSession(
                    session_id=session.session_id,
                    previous_status=previous_status,
                    requeued_task_ids=requeued_task_ids,
                )
            )

        return result

    def _expire_stale_sessions_in_repo(
        self,
        *,
        config: AppConfig,
        now: datetime,
    ) -> ExpireResult:
        snapshot = self.sessions_repo.load_snapshot()
        preview_state = snapshot.state.model_copy(deep=True)
        preview_result = self._expire_stale_sessions(
            state=preview_state,
            config=config,
            now=now,
        )
        if not preview_result.expired_sessions:
            return preview_result

        return self.sessions_repo.update_from_snapshot(
            snapshot,
            lambda state: self._expire_stale_sessions(
                state=state,
                config=config,
                now=now,
            ),
        )

    def _persist_expire_result(self, expire_result: ExpireResult) -> None:
        for expired_session in expire_result.expired_sessions:
            self.tasks_log.append(
                TaskEvent(
                    event_type="session_expired",
                    task_id=expired_session.requeued_task_ids[0]
                    if expired_session.requeued_task_ids
                    else "",
                    payload={
                        "session_id": expired_session.session_id,
                        "previous_status": expired_session.previous_status,
                        "reason": "stale_timeout",
                        "task_ids": expired_session.requeued_task_ids,
                    },
                )
            )

            for task_id in expired_session.requeued_task_ids:
                self._set_task_queue_status(task_id, QueueStatus.QUEUED_GLOBALLY)
                self.tasks_log.append(
                    TaskEvent(
                        event_type="task_requeued_from_dead_session",
                        task_id=task_id,
                        payload={
                            "session_id": expired_session.session_id,
                            "previous_status": expired_session.previous_status,
                            "reason": "stale_timeout",
                        },
                    )
                )

    def _reconcile_orphaned_running_tasks(self, *, config: AppConfig) -> None:
        state = self.sessions_repo.load()
        known_session_ids = {session.session_id for session in state.sessions}
        orphaned_tasks = [
            task
            for task in self.task_repo.list_by_status(QueueStatus.RUNNING)
            if task.queue_status is QueueStatus.RUNNING
            and task.routing.assigned_session_id is not None
            and task.routing.assigned_session_id not in known_session_ids
        ]
        if not orphaned_tasks:
            return

        worker = self._resolve_worker(config)
        for task in orphaned_tasks:
            session_id = task.routing.assigned_session_id
            if session_id is None:
                continue

            run_state = worker.poll(
                run_id=None,
                process_id=None,
                output_path=self.paths.worker_runs_dir / session_id / f"{task.task_id}.jsonl",
            )
            if run_state.is_running:
                continue

            queue_status = QueueStatus.COMPLETED if run_state.exit_code == 0 else QueueStatus.FAILED
            self._set_task_queue_status(task.task_id, queue_status)
            self.tasks_log.append(
                TaskEvent(
                    event_type="task_reconciled_from_orphaned_run",
                    task_id=task.task_id,
                    payload={
                        "session_id": session_id,
                        "queue_status": queue_status,
                        "exit_code": run_state.exit_code,
                        "summary": run_state.last_message,
                        "recovery_reason": run_state.recovery_reason,
                    },
                )
            )

    def _reconcile_orphaned_queued_session_tasks(self) -> list[QueuedTaskRecovery]:
        state = self.sessions_repo.load()
        sessions_by_id = {session.session_id: session for session in state.sessions}
        recoveries: list[QueuedTaskRecovery] = []

        for task in self.task_repo.list_by_status(QueueStatus.QUEUED_IN_SESSION):
            session_id = task.routing.assigned_session_id
            if session_id is None:
                recoveries.append(
                    QueuedTaskRecovery(
                        task_id=task.task_id,
                        session_id=None,
                        reason="missing_assigned_session",
                    )
                )
                continue

            session = sessions_by_id.get(session_id)
            if session is None:
                recoveries.append(
                    QueuedTaskRecovery(
                        task_id=task.task_id,
                        session_id=session_id,
                        reason="missing_session",
                    )
                )
                continue
            if session.status is SessionStatus.DEAD:
                recoveries.append(
                    QueuedTaskRecovery(
                        task_id=task.task_id,
                        session_id=session_id,
                        reason="dead_session",
                    )
                )
                continue
            if session.current_task_id == task.task_id:
                continue

            in_queue = task.task_id in session.queue
            has_history = any(entry.task_id == task.task_id for entry in session.task_history)
            if in_queue and has_history:
                continue

            recoveries.append(
                QueuedTaskRecovery(
                    task_id=task.task_id,
                    session_id=session_id,
                    reason="missing_session_queue" if not in_queue else "missing_session_history",
                )
            )

        if not recoveries:
            return []

        recovery_ids = {recovery.task_id for recovery in recoveries}

        def mutate(state: SessionsState) -> None:
            state.global_queue = [recovery.task_id for recovery in recoveries] + [
                task_id for task_id in state.global_queue if task_id not in recovery_ids
            ]

            for recovery in recoveries:
                session = next(
                    (item for item in state.sessions if item.session_id == recovery.session_id),
                    None,
                )
                if session is None:
                    continue

                session.queue = [task_id for task_id in session.queue if task_id != recovery.task_id]
                removed_history = _drop_task_history_entry(
                    session,
                    recovery.task_id,
                    revert_started_turn=True,
                )
                if not removed_history and session.turn_count > 0:
                    session.turn_count -= 1

        self.sessions_repo.update(mutate)

        for recovery in recoveries:
            self._set_task_queue_status(recovery.task_id, QueueStatus.QUEUED_GLOBALLY)
            self.tasks_log.append(
                TaskEvent(
                    event_type="task_requeued_from_orphaned_session_queue",
                    task_id=recovery.task_id,
                    payload={
                        "session_id": recovery.session_id,
                        "reason": recovery.reason,
                    },
                )
            )

        return recoveries

    def _requeue_dead_session_tasks(
        self,
        *,
        session: Session,
        state: SessionsState,
        now: datetime,
    ) -> list[str]:
        task_ids: list[str] = []

        if session.current_task_id is not None:
            task_ids.append(session.current_task_id)
            history_entry = _find_history_entry(session, session.current_task_id)
            if history_entry.status is TaskHistoryStatus.RUNNING:
                history_entry.status = TaskHistoryStatus.INTERRUPTED
                history_entry.summary = history_entry.summary or "stale session expired"
                history_entry.updated_at = now

        task_ids.extend(session.queue)
        if task_ids:
            state.global_queue = task_ids + state.global_queue

        session.current_task_id = None
        session.queue.clear()
        _clear_active_run(session)
        return task_ids

    def _collect_active_runs(self, state: SessionsState) -> list[ActiveRun]:
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

    def _apply_polled_runs(
        self,
        *,
        state: SessionsState,
        config: AppConfig,
        polled_runs: list[PolledRun],
    ) -> PollMutation:
        mutation = PollMutation()

        for polled_run in polled_runs:
            active_run = polled_run.active_run
            run_state = polled_run.run_state
            task = self._load_task(active_run.task_id)

            if run_state.is_running:
                updated = self._refresh_running_session(
                    state=state,
                    active_run=active_run,
                    run_state=run_state,
                )
                if updated:
                    mutation.poll_result.running += 1
                continue

            if run_state.requires_manual_recovery:
                retry_transition = self._retry_failed_task(
                    state=state,
                    config=config,
                    active_run=active_run,
                    run_state=run_state,
                    task=task,
                    history_status=TaskHistoryStatus.INTERRUPTED,
                )
                if retry_transition is not None:
                    mutation.poll_result.failed += 1
                    mutation.task_status_updates.append(
                        TaskQueueStatusUpdate(
                            task_id=active_run.task_id,
                            queue_status=QueueStatus.QUEUED_GLOBALLY,
                            retry_count=task.retry_count,
                        )
                    )
                    mutation.task_events.append(
                        TaskEvent(
                            event_type="task_retry_scheduled",
                            task_id=active_run.task_id,
                            payload={
                                "session_id": active_run.session_id,
                                "run_id": active_run.run_id,
                                "summary": run_state.last_message,
                                "reason": run_state.recovery_reason,
                                "retry_count": task.retry_count,
                                "max_retries": task.max_retries,
                            },
                        )
                    )
                    if retry_transition.next_dispatch is not None:
                        mutation.dispatch_requests.append(retry_transition.next_dispatch)
                    continue

                blocked = self._block_run(
                    state=state,
                    active_run=active_run,
                    run_state=run_state,
                )
                if blocked:
                    mutation.task_status_updates.append(
                        TaskQueueStatusUpdate(
                            task_id=active_run.task_id,
                            queue_status=QueueStatus.FAILED,
                        )
                    )
                    mutation.task_events.append(
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

            if run_state.exit_code == 0:
                transition = self._finalize_run(
                    state=state,
                    config=config,
                    active_run=active_run,
                    run_state=run_state,
                )
                mutation.poll_result.completed += 1
                mutation.task_status_updates.append(
                    TaskQueueStatusUpdate(
                        task_id=active_run.task_id,
                        queue_status=QueueStatus.COMPLETED,
                    )
                )
                event_type = "task_completed"
            else:
                retry_transition = self._retry_failed_task(
                    state=state,
                    config=config,
                    active_run=active_run,
                    run_state=run_state,
                    task=task,
                    history_status=TaskHistoryStatus.FAILED,
                )
                if retry_transition is not None:
                    mutation.poll_result.failed += 1
                    mutation.task_status_updates.append(
                        TaskQueueStatusUpdate(
                            task_id=active_run.task_id,
                            queue_status=QueueStatus.QUEUED_GLOBALLY,
                            retry_count=task.retry_count,
                        )
                    )
                    mutation.task_events.append(
                        TaskEvent(
                            event_type="task_retry_scheduled",
                            task_id=active_run.task_id,
                            payload={
                                "session_id": active_run.session_id,
                                "run_id": active_run.run_id,
                                "exit_code": run_state.exit_code,
                                "summary": run_state.last_message,
                                "retry_count": task.retry_count,
                                "max_retries": task.max_retries,
                            },
                        )
                    )
                    if retry_transition.next_dispatch is not None:
                        mutation.dispatch_requests.append(retry_transition.next_dispatch)
                    continue

                transition = self._finalize_run(
                    state=state,
                    config=config,
                    active_run=active_run,
                    run_state=run_state,
                )
                mutation.poll_result.failed += 1
                mutation.task_status_updates.append(
                    TaskQueueStatusUpdate(
                        task_id=active_run.task_id,
                        queue_status=QueueStatus.FAILED,
                    )
                )
                event_type = "task_failed"

            mutation.task_events.append(
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
                mutation.dispatch_requests.append(transition.next_dispatch)

        mutation.dispatch_requests.extend(self._reserve_dispatchable_global_tasks(state=state, config=config))
        return mutation

    def _retry_failed_task(
        self,
        *,
        state: SessionsState,
        config: AppConfig,
        active_run: ActiveRun,
        run_state: WorkerRunState,
        task: Task,
        history_status: TaskHistoryStatus,
    ) -> TransitionResult | None:
        if task.retry_count >= task.max_retries:
            return None

        session = _find_session(state, active_run.session_id)
        if not _session_matches_run(session, active_run):
            return None

        transition = self._close_running_task(
            config=config,
            session=session,
            task_id=active_run.task_id,
            next_status=history_status,
            run_state=run_state,
        )
        state.global_queue.insert(0, active_run.task_id)
        task.retry_count += 1
        return transition

    def _reserve_dispatchable_global_tasks(
        self,
        *,
        state: SessionsState,
        config: AppConfig,
        excluded_task_ids: set[str] | None = None,
    ) -> list[DispatchRequest]:
        requests: list[DispatchRequest] = []
        dispatch_limit = self._global_promotion_dispatch_limit_for_state(state, config)

        while len(requests) < dispatch_limit:
            request = self._reserve_global_dispatch(
                state=state,
                config=config,
                excluded_task_ids=excluded_task_ids,
            )
            if request is None:
                break
            requests.append(request)

        return requests

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
        config: AppConfig,
        active_run: ActiveRun,
        run_state: WorkerRunState,
    ) -> TransitionResult:
        session = _find_session(state, active_run.session_id)
        if not _session_matches_run(session, active_run):
            return TransitionResult()

        if run_state.exit_code == 0:
            return self._complete_running_task(
                config=config,
                session=session,
                task_id=active_run.task_id,
                run_state=run_state,
            )
        return self._fail_running_task(
            config=config,
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

        if _should_reset_worker_context(run_state):
            session.worker_context_id = None
        elif run_state.worker_context_id:
            session.worker_context_id = run_state.worker_context_id
        session.status = SessionStatus.BLOCKED
        session.last_heartbeat = now
        _clear_active_run(session)
        return True

    def _complete_running_task(
        self,
        *,
        config: AppConfig,
        session: Session,
        task_id: str,
        run_state: WorkerRunState,
    ) -> TransitionResult:
        return self._close_running_task(
            config=config,
            session=session,
            task_id=task_id,
            next_status=TaskHistoryStatus.COMPLETED,
            run_state=run_state,
        )

    def _fail_running_task(
        self,
        *,
        config: AppConfig,
        session: Session,
        task_id: str,
        run_state: WorkerRunState,
    ) -> TransitionResult:
        return self._close_running_task(
            config=config,
            session=session,
            task_id=task_id,
            next_status=TaskHistoryStatus.FAILED,
            run_state=run_state,
        )

    def _close_running_task(
        self,
        *,
        config: AppConfig,
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

        if _should_reset_worker_context(run_state):
            session.worker_context_id = None
        elif run_state.worker_context_id:
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
                    original_queue_source="session",
                    promoted_from_queue="session",
                )
            else:
                session.status = SessionStatus.IDLE

        return TransitionResult(next_dispatch=next_dispatch)

    def _promote_next_global_task(
        self,
        config: AppConfig,
        *,
        excluded_task_ids: set[str] | None = None,
    ) -> bool:
        request = self.sessions_repo.update(
            lambda state: self._reserve_global_dispatch(
                state=state,
                config=config,
                excluded_task_ids=excluded_task_ids,
            )
        )
        if request is None:
            return False
        return self._dispatch_with_rollback(config=config, request=request)

    def _promote_dispatchable_global_tasks(
        self,
        config: AppConfig,
        *,
        excluded_task_ids: set[str] | None = None,
    ) -> int:
        requests = self.sessions_repo.update(
            lambda state: self._reserve_dispatchable_global_tasks(
                state=state,
                config=config,
                excluded_task_ids=excluded_task_ids,
            )
        )
        dispatched = 0
        for request in requests:
            if self._dispatch_with_rollback(config=config, request=request):
                dispatched += 1
        return dispatched

    def _global_promotion_dispatch_limit(self, config: AppConfig) -> int:
        state = self.sessions_repo.load()
        return self._global_promotion_dispatch_limit_for_state(state, config)

    def _global_promotion_dispatch_limit_for_state(
        self,
        state: SessionsState,
        config: AppConfig,
    ) -> int:
        idle_sessions = sum(
            1
            for session in state.sessions
            if _session_can_accept_new_turn(session, config)
        )
        remaining_capacity = max(config.max_terminal_num - live_session_count(state.sessions), 0)
        return idle_sessions + remaining_capacity

    def _reserve_global_dispatch(
        self,
        *,
        state: SessionsState,
        config: AppConfig,
        excluded_task_ids: set[str] | None = None,
    ) -> DispatchRequest | None:
        if not state.global_queue:
            return None

        task_id = state.global_queue[0]
        if excluded_task_ids and task_id in excluded_task_ids:
            return None

        idle_session = next(
            (
                session
                for session in state.sessions
                if _session_can_accept_new_turn(session, config)
            ),
            None,
        )
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
        session.turn_count += 1
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
            original_queue_source=promoted_from_queue or "direct",
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
            turn_count=1,
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
            original_queue_source=promoted_from_queue or "direct",
            promoted_from_queue=promoted_from_queue,
            created_session=True,
        )

    def _resolve_worker(self, config: AppConfig) -> WorkerPort:
        desired_backend = self._selected_backend(config)
        if self.worker is None or (
            not self._worker_is_external and self._resolved_worker_backend is not desired_backend
        ):
            self.worker = build_worker(config, self.worker_backend)
            self._resolved_worker_backend = desired_backend
        return self.worker

    def _resolve_task_note_port(self, config: AppConfig) -> TaskNotePort:
        if self.task_note_port is None:
            self.task_note_port = AgentTaskNoteGenerator(config.router.agent)
        return self.task_note_port

    def _resolve_router(self, config: AppConfig) -> ContextRouter:
        if self.router is None:
            self.router = build_router(config)
        return self.router

    def _dispatch_task(
        self,
        *,
        config: AppConfig,
        request: DispatchRequest,
        dispatch_context: DispatchContext,
    ) -> WorkerSubmission:
        worker = self._resolve_worker(config)

        submission = worker.submit(
            request.session_id,
            request.task_id,
            request.user_prompt,
            cwd=dispatch_context.cwd,
            data_dir=self.paths.root_dir,
            worker_context_id=dispatch_context.worker_context_id,
        )

        def persist_submission(state: SessionsState) -> None:
            current_session = _find_session(state, request.session_id)
            now = datetime.now(timezone.utc)
            current_session.worker_backend = dispatch_context.backend
            current_session.worker_context_id = (
                submission.worker_context_id or dispatch_context.worker_context_id
            )
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
                    "worker_context_id": submission.worker_context_id or dispatch_context.worker_context_id,
                    "cwd": str(dispatch_context.cwd),
                    "output_path": submission.output_path,
                },
            )
        )
        return submission

    def _dispatch_with_rollback(
        self,
        *,
        config: AppConfig,
        request: DispatchRequest,
        propagate_error: bool = False,
    ) -> bool:
        self._set_task_queue_status(request.task_id, QueueStatus.RUNNING)
        dispatch_context: DispatchContext | None = None
        try:
            dispatch_context = self._prepare_dispatch_context(config=config, request=request)
            self._dispatch_task(
                config=config,
                request=request,
                dispatch_context=dispatch_context,
            )
        except Exception as exc:
            self._rollback_failed_dispatch(
                config=config,
                request=request,
                error=exc,
                dispatch_context=dispatch_context,
            )
            if propagate_error:
                raise
            return False
        return True

    def _prepare_dispatch_context(self, *, config: AppConfig, request: DispatchRequest) -> DispatchContext:
        session = _find_session(self.sessions_repo.load(), request.session_id)
        selected_backend = self._selected_backend(config).value
        dispatch_context = DispatchContext(
            cwd=self.cwd,
            worker_context_id=(
                session.worker_context_id if session.worker_backend == selected_backend else None
            ),
            backend=selected_backend,
        )
        if not config.worktree.enabled:
            return dispatch_context

        worktree = self._worktree_manager(config).ensure(
            session_id=session.session_id,
            existing_path=session.worktree_path,
            existing_branch=session.worktree_branch,
            existing_base_ref=session.worktree_base_ref,
        )
        self.sessions_repo.update(
            lambda state: self._persist_session_worktree(
                state=state,
                session_id=session.session_id,
                worktree=worktree,
            )
        )
        dispatch_context.cwd = Path(worktree.path)
        dispatch_context.worktree = worktree
        return dispatch_context

    def _persist_session_worktree(
        self,
        *,
        state: SessionsState,
        session_id: str,
        worktree: SessionWorktree,
    ) -> None:
        session = _find_session(state, session_id)
        session.worktree_path = worktree.path
        session.worktree_branch = worktree.branch
        session.worktree_base_ref = worktree.base_ref

    def _rollback_failed_dispatch(
        self,
        *,
        config: AppConfig,
        request: DispatchRequest,
        error: Exception,
        dispatch_context: DispatchContext | None = None,
    ) -> None:
        now = datetime.now(timezone.utc)
        cleanup_error: str | None = None

        if dispatch_context and dispatch_context.worktree and dispatch_context.worktree.created:
            try:
                self._worktree_manager(config).remove(
                    path=dispatch_context.worktree.path,
                    branch=dispatch_context.worktree.branch,
                    remove_branch=dispatch_context.worktree.created_branch,
                )
            except Exception as exc:
                cleanup_error = str(exc)

        def mutate(state: SessionsState) -> None:
            state.global_queue = [task_id for task_id in state.global_queue if task_id != request.task_id]
            state.global_queue.insert(0, request.task_id)

            session = next((item for item in state.sessions if item.session_id == request.session_id), None)
            if session is None:
                return

            session.queue = [task_id for task_id in session.queue if task_id != request.task_id]
            if session.current_task_id == request.task_id:
                session.current_task_id = None
            _drop_task_history_entry(session, request.task_id, revert_started_turn=True)
            _clear_active_run(session)
            session.last_heartbeat = now
            if dispatch_context and dispatch_context.worktree and dispatch_context.worktree.created and cleanup_error is None:
                session.worktree_path = None
                session.worktree_branch = None
                session.worktree_base_ref = None

            if request.created_session and _session_has_no_work(session):
                state.sessions = [item for item in state.sessions if item.session_id != session.session_id]
                return

            session.status = SessionStatus.IDLE

        self.sessions_repo.update(mutate)
        self._set_task_queue_status(request.task_id, QueueStatus.QUEUED_GLOBALLY)
        self.tasks_log.append(
            TaskEvent(
                event_type="task_dispatch_failed",
                task_id=request.task_id,
                payload={
                    "session_id": request.session_id,
                    "source": request.original_queue_source,
                    "created_session": request.created_session,
                    "error": str(error),
                    "cleanup_error": cleanup_error,
                },
            )
        )

    def _restore_reserved_dispatch_requests(
        self,
        *,
        failed_request: DispatchRequest,
        requests: list[DispatchRequest],
    ) -> None:
        if not requests:
            return

        task_ids = [request.task_id for request in requests]
        now = datetime.now(timezone.utc)

        def mutate(state: SessionsState) -> None:
            insert_at = 0
            if state.global_queue and state.global_queue[0] == failed_request.task_id:
                insert_at = 1
            state.global_queue[insert_at:insert_at] = task_ids

            for request in requests:
                session = next((item for item in state.sessions if item.session_id == request.session_id), None)
                if session is None:
                    continue

                session.queue = [task_id for task_id in session.queue if task_id != request.task_id]
                if session.current_task_id == request.task_id:
                    session.current_task_id = None
                _drop_task_history_entry(session, request.task_id, revert_started_turn=True)
                _clear_active_run(session)
                session.last_heartbeat = now

                if request.created_session and _session_has_no_work(session):
                    state.sessions = [item for item in state.sessions if item.session_id != session.session_id]
                    continue

                session.status = SessionStatus.IDLE

        self.sessions_repo.update(mutate)

    def _update_task_snapshot(
        self,
        task_id: str,
        *,
        queue_status: QueueStatus | None = None,
        routing: TaskRouting | None = None,
        retry_count: int | None = None,
    ) -> Task:
        task = self._load_task(task_id)
        if queue_status is not None:
            task.queue_status = queue_status
        if routing is not None:
            task.routing = routing
        if retry_count is not None:
            task.retry_count = retry_count
        self.task_repo.save(task)
        return task

    def _set_task_queue_status(self, task_id: str, queue_status: QueueStatus) -> None:
        self._update_task_snapshot(task_id, queue_status=queue_status)

    def _selected_backend(self, config: AppConfig) -> WorkerBackend:
        return self.worker_backend or config.worker.backend

    def _worktree_manager(self, config: AppConfig) -> WorktreeManager:
        return WorktreeManager(cwd=self.cwd, config=config.worktree)

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


def _session_has_active_run(session: Session) -> bool:
    return any(
        value is not None
        for value in (
            session.active_run_id,
            session.active_pid,
            session.active_run_started_at,
            session.last_run_output_path,
        )
    )


def _clear_active_run(session: Session) -> None:
    session.active_run_id = None
    session.active_pid = None
    session.active_run_started_at = None
    session.last_run_output_path = None


def _drop_task_history_entry(
    session: Session,
    task_id: str,
    *,
    revert_started_turn: bool = False,
) -> bool:
    removed = any(entry.task_id == task_id for entry in session.task_history)
    session.task_history = [entry for entry in session.task_history if entry.task_id != task_id]
    if removed and revert_started_turn and session.turn_count > 0:
        session.turn_count -= 1
    return removed


def _session_has_no_work(session: Session) -> bool:
    return session.current_task_id is None and not session.queue and not session.task_history


def _session_can_accept_new_turn(session: Session, config: AppConfig) -> bool:
    return (
        session.status is SessionStatus.IDLE
        and session.current_task_id is None
        and session.turn_count < config.router.agent.max_turns_per_session
    )


def _should_reset_worker_context(run_state: WorkerRunState) -> bool:
    message = (run_state.last_message or "").lower()
    return "unknown session_id" in message or "unknown session id" in message


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
        if session.turn_count >= config.router.agent.max_turns_per_session:
            raise RoutingError(f"Router returned session_id at turn limit: {route.session_id}")
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
