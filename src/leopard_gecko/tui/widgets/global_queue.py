from __future__ import annotations

from typing import TYPE_CHECKING

from textual.containers import VerticalScroll
from textual.widgets import Static

from leopard_gecko.models.session import SessionsState

if TYPE_CHECKING:
    from leopard_gecko.store.task_repo import TaskRepository


class GlobalQueuePanel(VerticalScroll):
    _task_repo: TaskRepository | None = None

    def compose(self):
        yield Static("[bold]Global Queue[/bold]", id="gq-title")
        yield Static("[dim]Empty[/dim]", id="gq-content")

    def on_mount(self) -> None:
        from leopard_gecko.tui.app import LeopardGeckoApp

        app: LeopardGeckoApp = self.app  # type: ignore[assignment]
        if app.orchestrator:
            self._task_repo = app.orchestrator.task_repo

    def update_from_state(self, state: SessionsState) -> None:
        content = self.query_one("#gq-content", Static)
        if not state.global_queue:
            content.update("[dim]Empty[/dim]")
            return

        lines: list[str] = []
        for task_id in state.global_queue[:20]:
            prompt_preview = self._load_prompt_preview(task_id)
            lines.append(f"  {task_id}: {prompt_preview}")

        if len(state.global_queue) > 20:
            lines.append(f"  ... +{len(state.global_queue) - 20} more")

        content.update("\n".join(lines))

    def _load_prompt_preview(self, task_id: str) -> str:
        if not self._task_repo:
            return "-"
        try:
            task = self._task_repo.load(task_id)
            prompt = task.user_prompt
            if len(prompt) > 40:
                return prompt[:39] + "\u2026"
            return prompt
        except Exception:
            return "-"
