from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.css.query import NoMatches
from textual.screen import Screen
from textual.widgets import Footer, Header, Static

from leopard_gecko.models.session import Session, SessionsState, TaskHistoryEntry
from leopard_gecko.models.task import QueueStatus, Task
from leopard_gecko.tui.widgets.status_bar import StatusBar
from leopard_gecko.tui.widgets.task_detail_panel import TaskDetailPanel
from leopard_gecko.tui.widgets.task_list import TaskList
from leopard_gecko.tui.widgets.worker_output import WorkerOutputLog

if TYPE_CHECKING:
    from leopard_gecko.tui.app import LeopardGeckoApp


class DetailScreen(Screen):
    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("No prompt selected", id="detail-header")
        with Horizontal(id="detail-body"):
            yield TaskList(id="task-list")
            yield TaskDetailPanel(id="task-detail-panel")
        yield WorkerOutputLog(highlight=True, markup=True, id="detail-output")
        yield StatusBar("Detail")
        yield Footer()

    def on_mount(self) -> None:
        self._refresh_detail()

    def on_screen_resume(self) -> None:
        self._refresh_detail()

    def refresh_state(self) -> None:
        self._refresh_detail()

    def on_task_list_selected(self, event: TaskList.Selected) -> None:
        app: LeopardGeckoApp = self.app  # type: ignore[assignment]
        app.selected_task_id = event.task.task_id

        history_entry, session = self._find_task_context(
            event.task, app.current_state or SessionsState()
        )
        app.selected_session_id = session.session_id if session else event.task.routing.assigned_session_id
        self._refresh_detail()

    def _refresh_detail(self) -> None:
        app: LeopardGeckoApp = self.app  # type: ignore[assignment]
        try:
            header = self.query_one("#detail-header", Static)
            task_list = self.query_one("#task-list", TaskList)
            panel = self.query_one("#task-detail-panel", TaskDetailPanel)
            output_log = self.query_one("#detail-output", WorkerOutputLog)
        except NoMatches:
            return

        tasks = self._load_tasks(app)
        task_list.refresh_from_tasks(tasks)

        if not tasks:
            panel.clear_panel()
            output_log.display = False
            header.update("[dim]No prompts recorded yet. Submit a task first, then open detail.[/dim]")
            return

        task = self._resolve_task(app, tasks)
        if task is None:
            panel.clear_panel()
            output_log.display = False
            header.update("[dim]No prompts recorded yet. Submit a task first, then open detail.[/dim]")
            return

        if task_list._tasks and task_list.highlighted is not None:
            current_index = task_list.highlighted
            if not (0 <= current_index < len(task_list._tasks)) or task_list._tasks[current_index].task_id != task.task_id:
                for index, item in enumerate(task_list._tasks):
                    if item.task_id == task.task_id:
                        task_list.highlighted = index
                        break

        state = app.current_state or SessionsState()
        history_entry, session = self._find_task_context(task, state)
        app.selected_session_id = session.session_id if session else task.routing.assigned_session_id

        header.update(self._build_header(tasks, task, session))
        panel.show_task(task, history_entry=history_entry, session=session)

        output_log.update_state(state)
        if task.queue_status is QueueStatus.RUNNING and session and session.last_run_output_path:
            output_log.display = True
            if output_log._current_session_id != session.session_id:
                output_log.watch_session(session.session_id)
        else:
            output_log.display = False

        self.query_one(StatusBar).update_from_state(state)

    def _load_tasks(self, app: LeopardGeckoApp) -> list[Task]:
        if not app.orchestrator:
            return []
        try:
            tasks = app.orchestrator.task_repo.list_all()
        except Exception:
            return []
        return sorted(tasks, key=lambda item: (item.created_at, item.task_id), reverse=True)

    def _resolve_task(self, app: LeopardGeckoApp, tasks: list[Task]) -> Task | None:
        if app.selected_task_id:
            for task in tasks:
                if task.task_id == app.selected_task_id:
                    return task
        if not tasks:
            app.selected_task_id = None
            return None

        app.selected_task_id = tasks[0].task_id
        return tasks[0]

    def _find_task_context(
        self,
        task: Task,
        state: SessionsState,
    ) -> tuple[TaskHistoryEntry | None, Session | None]:
        if task.routing.assigned_session_id:
            for session in state.sessions:
                if session.session_id != task.routing.assigned_session_id:
                    continue
                entry = self._find_history_entry(session, task.task_id)
                return entry, session

        for session in state.sessions:
            entry = self._find_history_entry(session, task.task_id)
            if entry is not None or session.current_task_id == task.task_id:
                return entry, session

        return None, None

    def _find_history_entry(self, session: Session, task_id: str) -> TaskHistoryEntry | None:
        for entry in reversed(session.task_history):
            if entry.task_id == task_id:
                return entry
        return None

    def _build_header(self, tasks: list[Task], selected_task: Task, session: Session | None) -> str:
        running = sum(1 for task in tasks if task.queue_status is QueueStatus.RUNNING)
        queued = sum(
            1
            for task in tasks
            if task.queue_status
            in {
                QueueStatus.PENDING,
                QueueStatus.QUEUED_IN_SESSION,
                QueueStatus.QUEUED_GLOBALLY,
            }
        )
        completed = sum(1 for task in tasks if task.queue_status is QueueStatus.COMPLETED)
        failed = sum(1 for task in tasks if task.queue_status is QueueStatus.FAILED)

        parts = [
            f"[bold]Prompts:[/bold] {len(tasks)}",
            f"[bold]Running:[/bold] {running}",
            f"[bold]Queued:[/bold] {queued}",
            f"[bold]Completed:[/bold] {completed}",
            f"[bold]Failed:[/bold] {failed}",
            f"[bold]Selected:[/bold] {selected_task.task_id}",
        ]
        if session:
            parts.append(f"[bold]Session:[/bold] {session.session_id}")
        elif selected_task.routing.assigned_session_id:
            parts.append(f"[bold]Session:[/bold] {selected_task.routing.assigned_session_id}")

        return "  ".join(parts)
