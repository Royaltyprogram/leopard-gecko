from __future__ import annotations

from textual.message import Message
from textual.widgets import OptionList
from textual.widgets.option_list import Option

from leopard_gecko.models.session import TaskHistoryEntry, TaskHistoryStatus


STATUS_ICON = {
    TaskHistoryStatus.COMPLETED: "\u2714",
    TaskHistoryStatus.RUNNING: "\u25b6",
    TaskHistoryStatus.FAILED: "\u2718",
    TaskHistoryStatus.QUEUED: "\u23f3",
    TaskHistoryStatus.INTERRUPTED: "\u26a0",
}

STATUS_COLOR = {
    TaskHistoryStatus.COMPLETED: "green",
    TaskHistoryStatus.RUNNING: "yellow",
    TaskHistoryStatus.FAILED: "red",
    TaskHistoryStatus.QUEUED: "cyan",
    TaskHistoryStatus.INTERRUPTED: "magenta",
}


class TaskSelected(Message):
    def __init__(self, entry: TaskHistoryEntry) -> None:
        super().__init__()
        self.entry = entry


class TaskHistoryList(OptionList):
    _entries: list[TaskHistoryEntry] = []

    class Selected(Message):
        """Posted when a task is highlighted."""

        def __init__(self, entry: TaskHistoryEntry) -> None:
            super().__init__()
            self.entry = entry

    def refresh_from_history(self, task_history: list[TaskHistoryEntry]) -> None:
        prev_index = self.highlighted
        self._entries = list(reversed(task_history))
        self.clear_options()
        for entry in self._entries:
            icon = STATUS_ICON.get(entry.status, " ")
            color = STATUS_COLOR.get(entry.status, "")
            prompt_short = entry.user_prompt[:40]
            if len(entry.user_prompt) > 40:
                prompt_short += "\u2026"
            label = f"[{color}]{icon}[/{color}] {prompt_short}"
            self.add_option(Option(label, id=entry.task_id))
        if self._entries:
            target = min(prev_index or 0, len(self._entries) - 1)
            self.highlighted = target
        else:
            self.highlighted = None

    def on_option_list_option_highlighted(
        self, event: OptionList.OptionHighlighted
    ) -> None:
        if event.option_index is not None and event.option_index < len(self._entries):
            self.post_message(self.Selected(self._entries[event.option_index]))
