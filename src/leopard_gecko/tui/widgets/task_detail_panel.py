from __future__ import annotations

from textual.containers import VerticalScroll
from textual.widgets import Static

from leopard_gecko.models.session import Session, TaskHistoryEntry, TaskHistoryStatus
from leopard_gecko.models.task import QueueStatus, RoutingDecision, Task

HISTORY_STATUS_COLOR = {
    TaskHistoryStatus.COMPLETED: "green",
    TaskHistoryStatus.RUNNING: "yellow",
    TaskHistoryStatus.FAILED: "red",
    TaskHistoryStatus.QUEUED: "cyan",
    TaskHistoryStatus.INTERRUPTED: "magenta",
}

TASK_STATUS_COLOR = {
    QueueStatus.PENDING: "white",
    QueueStatus.QUEUED_IN_SESSION: "cyan",
    QueueStatus.QUEUED_GLOBALLY: "cyan",
    QueueStatus.RUNNING: "yellow",
    QueueStatus.COMPLETED: "green",
    QueueStatus.FAILED: "red",
}


class TaskDetailPanel(VerticalScroll):
    DEFAULT_CSS = """
    TaskDetailPanel {
        padding: 1 2;
    }
    TaskDetailPanel #task-meta {
        height: auto;
        margin-bottom: 1;
    }
    TaskDetailPanel #task-prompt-header {
        text-style: bold;
        color: $text;
        margin-bottom: 0;
    }
    TaskDetailPanel #task-prompt-body {
        height: auto;
        margin-bottom: 1;
        padding: 1;
        background: $surface;
    }
    TaskDetailPanel #task-response-header {
        text-style: bold;
        color: $text;
        margin-bottom: 0;
    }
    TaskDetailPanel #task-response-body {
        height: auto;
        padding: 1;
        background: $surface;
    }
    """

    def compose(self):
        yield Static("[dim]Select a task from the list[/dim]", id="task-meta")
        yield Static("", id="task-prompt-header")
        yield Static("", id="task-prompt-body")
        yield Static("", id="task-response-header")
        yield Static("", id="task-response-body")

    def show_entry(self, entry: TaskHistoryEntry) -> None:
        color = HISTORY_STATUS_COLOR.get(entry.status, "")
        status_tag = f"[{color}]{entry.status.value}[/{color}]" if color else entry.status.value
        ts = entry.updated_at.strftime("%Y-%m-%d %H:%M:%S")

        self.query_one("#task-meta", Static).update(
            f"[bold]{entry.task_id}[/bold]  {status_tag}  [dim]{ts}[/dim]"
        )
        self.query_one("#task-prompt-header", Static).update("[bold]Prompt[/bold]")
        self.query_one("#task-prompt-body", Static).update(entry.user_prompt)

        self.query_one("#task-response-header", Static).update("[bold]Response[/bold]")
        if entry.summary:
            self.query_one("#task-response-body", Static).update(entry.summary)
        elif entry.status is TaskHistoryStatus.RUNNING:
            self.query_one("#task-response-body", Static).update(
                "[dim][italic]Task is currently running\u2026[/italic][/dim]"
            )
        elif entry.status is TaskHistoryStatus.QUEUED:
            self.query_one("#task-response-body", Static).update(
                "[dim][italic]Task is queued[/italic][/dim]"
            )
        else:
            self.query_one("#task-response-body", Static).update(
                "[dim]No response recorded[/dim]"
            )

    def show_task(
        self,
        task: Task,
        *,
        history_entry: TaskHistoryEntry | None = None,
        session: Session | None = None,
    ) -> None:
        color = TASK_STATUS_COLOR.get(task.queue_status, "")
        status_tag = (
            f"[{color}]{task.queue_status.value}[/{color}]"
            if color
            else task.queue_status.value
        )
        ts = task.created_at.strftime("%Y-%m-%d %H:%M:%S")

        meta_parts = [f"[bold]{task.task_id}[/bold]", status_tag, f"[dim]{ts}[/dim]"]
        if task.routing.assigned_session_id:
            meta_parts.append(f"[bold]session[/bold] {task.routing.assigned_session_id}")
        elif session:
            meta_parts.append(f"[bold]session[/bold] {session.session_id}")
        if task.routing.decision is not RoutingDecision.PENDING:
            meta_parts.append(f"[bold]route[/bold] {task.routing.decision.value}")

        self.query_one("#task-meta", Static).update("  ".join(meta_parts))
        self.query_one("#task-prompt-header", Static).update("[bold]Prompt[/bold]")
        self.query_one("#task-prompt-body", Static).update(task.user_prompt)
        self.query_one("#task-response-header", Static).update("[bold]Response[/bold]")

        if history_entry and history_entry.summary:
            self.query_one("#task-response-body", Static).update(history_entry.summary)
            return

        if task.queue_status is QueueStatus.RUNNING:
            self.query_one("#task-response-body", Static).update(
                "[dim][italic]Task is currently running\u2026[/italic][/dim]"
            )
            return

        if task.queue_status in {
            QueueStatus.PENDING,
            QueueStatus.QUEUED_IN_SESSION,
            QueueStatus.QUEUED_GLOBALLY,
        }:
            self.query_one("#task-response-body", Static).update(
                "[dim][italic]Task is queued[/italic][/dim]"
            )
            return

        self.query_one("#task-response-body", Static).update("[dim]No response recorded[/dim]")

    def clear_panel(self) -> None:
        self.query_one("#task-meta", Static).update(
            "[dim]Select a task from the list[/dim]"
        )
        self.query_one("#task-prompt-header", Static).update("")
        self.query_one("#task-prompt-body", Static).update("")
        self.query_one("#task-response-header", Static).update("")
        self.query_one("#task-response-body", Static).update("")
