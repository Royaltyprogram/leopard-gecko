from __future__ import annotations

from textual.message import Message
from textual.widgets import OptionList
from textual.widgets.option_list import Option

from leopard_gecko.models.task import QueueStatus, Task


STATUS_ICON = {
    QueueStatus.PENDING: "•",
    QueueStatus.QUEUED_IN_SESSION: "⏳",
    QueueStatus.QUEUED_GLOBALLY: "☰",
    QueueStatus.RUNNING: "▶",
    QueueStatus.COMPLETED: "✔",
    QueueStatus.FAILED: "✘",
}

STATUS_COLOR = {
    QueueStatus.PENDING: "white",
    QueueStatus.QUEUED_IN_SESSION: "cyan",
    QueueStatus.QUEUED_GLOBALLY: "cyan",
    QueueStatus.RUNNING: "yellow",
    QueueStatus.COMPLETED: "green",
    QueueStatus.FAILED: "red",
}


class TaskList(OptionList):
    _tasks: list[Task] = []
    _task_signature: tuple[tuple[str, str, str, str], ...] = ()

    class Selected(Message):
        def __init__(self, task: Task) -> None:
            super().__init__()
            self.task = task

    def refresh_from_tasks(self, tasks: list[Task]) -> None:
        previous_task_id = None
        if self._tasks and self.highlighted is not None and 0 <= self.highlighted < len(self._tasks):
            previous_task_id = self._tasks[self.highlighted].task_id

        self._tasks = tasks
        signature = tuple(
            (
                task.task_id,
                task.queue_status.value,
                task.routing.assigned_session_id or "",
                task.user_prompt,
            )
            for task in self._tasks
        )
        if signature == self._task_signature:
            if not self._tasks:
                self.highlighted = None
                return
            if previous_task_id:
                for index, task in enumerate(self._tasks):
                    if task.task_id == previous_task_id:
                        self.highlighted = index
                        return
            if self.highlighted is None:
                self.highlighted = 0
            return

        self._task_signature = signature
        self.clear_options()

        for task in self._tasks:
            icon = STATUS_ICON.get(task.queue_status, " ")
            color = STATUS_COLOR.get(task.queue_status, "")
            prompt_short = task.user_prompt[:40]
            if len(task.user_prompt) > 40:
                prompt_short += "\u2026"
            session_suffix = ""
            if task.routing.assigned_session_id:
                session_suffix = f" [dim]({task.routing.assigned_session_id})[/dim]"
            label = f"[{color}]{icon}[/{color}] {prompt_short}{session_suffix}"
            self.add_option(Option(label, id=task.task_id))

        if not self._tasks:
            self.highlighted = None
            return

        if previous_task_id:
            for index, task in enumerate(self._tasks):
                if task.task_id == previous_task_id:
                    self.highlighted = index
                    return

        self.highlighted = 0

    def on_option_list_option_highlighted(
        self, event: OptionList.OptionHighlighted
    ) -> None:
        if event.option_index is not None and event.option_index < len(self._tasks):
            self.post_message(self.Selected(self._tasks[event.option_index]))
