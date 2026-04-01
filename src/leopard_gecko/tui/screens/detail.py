from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Header, Static

from leopard_gecko.models.session import Session, SessionStatus, TaskHistoryStatus
from leopard_gecko.tui.widgets.status_bar import StatusBar
from leopard_gecko.tui.widgets.worker_output import WorkerOutputLog

if TYPE_CHECKING:
    from leopard_gecko.tui.app import LeopardGeckoApp


HISTORY_STYLE = {
    TaskHistoryStatus.COMPLETED: "green",
    TaskHistoryStatus.RUNNING: "yellow",
    TaskHistoryStatus.FAILED: "red",
    TaskHistoryStatus.QUEUED: "cyan",
    TaskHistoryStatus.INTERRUPTED: "magenta",
}

STATUS_COLOR = {
    SessionStatus.IDLE: "green",
    SessionStatus.BUSY: "yellow",
    SessionStatus.COOLDOWN: "cyan",
    SessionStatus.BLOCKED: "red",
    SessionStatus.DEAD: "dim",
}


class DetailScreen(Screen):
    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("No session selected", id="detail-header")
        yield WorkerOutputLog(highlight=True, markup=True, id="detail-output")
        with VerticalScroll(id="detail-history"):
            yield Static("[bold]Task History[/bold]", id="detail-history-title")
            yield Static("[dim]Empty[/dim]", id="detail-history-content")
        yield StatusBar("Detail")
        yield Footer()

    def on_mount(self) -> None:
        self._refresh_detail()

    def on_screen_resume(self) -> None:
        self._refresh_detail()

    def refresh_state(self) -> None:
        self._refresh_detail()

    def _refresh_detail(self) -> None:
        app: LeopardGeckoApp = self.app  # type: ignore[assignment]
        header = self.query_one("#detail-header", Static)

        if not app.selected_session_id or not app.current_state:
            header.update(
                "[dim]No session selected. Press [bold]1[/bold] to go to Dashboard.[/dim]"
            )
            return

        session = self._find_session(app)
        if not session:
            header.update(
                f"[yellow]Session {app.selected_session_id} completed and was removed.[/yellow]\n"
                "[dim]Noop worker finishes instantly — session gets cleaned up. "
                "Use codex backend for persistent sessions.\n"
                "Press [bold]esc[/bold] to return to Dashboard.[/dim]"
            )
            app.selected_session_id = None
            return

        # Header info
        color = STATUS_COLOR.get(session.status, "")
        lines = [
            f"[bold]Session:[/bold] {session.session_id}  [{color}]{session.status.value}[/{color}]",
            f"[bold]Worker:[/bold] {session.worker_backend or '-'}",
        ]
        if session.worktree_branch:
            lines.append(f"[bold]Branch:[/bold] {session.worktree_branch}")
        lines.append(
            f"[bold]Queue:[/bold] {len(session.queue)}  "
            f"[bold]History:[/bold] {len(session.task_history)}"
        )
        if session.current_task_id:
            lines.append(f"[bold]Current Task:[/bold] {session.current_task_id}")
        header.update("\n".join(lines))

        # Worker output
        output_log = self.query_one("#detail-output", WorkerOutputLog)
        output_log.update_state(app.current_state)
        if output_log._current_session_id != app.selected_session_id:
            output_log.watch_session(app.selected_session_id)

        # History
        history_content = self.query_one("#detail-history-content", Static)
        if not session.task_history:
            history_content.update("[dim]No history[/dim]")
        else:
            history_lines = []
            for entry in reversed(session.task_history[-15:]):
                style = HISTORY_STYLE.get(entry.status, "")
                tag = (
                    f"[{style}]{entry.status.value:12}[/{style}]"
                    if style
                    else f"{entry.status.value:12}"
                )
                prompt_short = entry.user_prompt[:50]
                if len(entry.user_prompt) > 50:
                    prompt_short += "\u2026"
                history_lines.append(f"  {tag} {prompt_short}")
            history_content.update("\n".join(history_lines))

        # Status bar
        if app.current_state:
            self.query_one(StatusBar).update_from_state(app.current_state)

    def _find_session(self, app: LeopardGeckoApp) -> Session | None:
        if not app.current_state:
            return None
        for s in app.current_state.sessions:
            if s.session_id == app.selected_session_id:
                return s
        return None
