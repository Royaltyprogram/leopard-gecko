from textual.message import Message
from textual.widgets import DataTable

from leopard_gecko.models.session import SessionsState, SessionStatus


STATUS_STYLE = {
    SessionStatus.IDLE: "green",
    SessionStatus.BUSY: "yellow",
    SessionStatus.BLOCKED: "red",
    SessionStatus.DEAD: "dim",
}


class SessionSelected(Message):
    def __init__(self, session_id: str) -> None:
        super().__init__()
        self.session_id = session_id


class SessionTable(DataTable):
    _session_ids: list[str]

    def on_mount(self) -> None:
        self._session_ids = []
        self.add_columns("session_id", "status", "current_task", "queue", "history", "heartbeat")
        self.cursor_type = "row"

    def refresh_from_state(self, state: SessionsState) -> None:
        prev_cursor = self._selected_session_id()
        self.clear()
        self._session_ids = []

        for session in state.sessions:
            style = STATUS_STYLE.get(session.status, "")
            styled_status = (
                f"[{style}]{session.status.value}[/{style}]" if style else session.status.value
            )
            self.add_row(
                session.session_id,
                styled_status,
                _truncate(session.current_task_id or "-", 20),
                str(len(session.queue)),
                str(len(session.task_history)),
                session.last_heartbeat.strftime("%H:%M:%S"),
            )
            self._session_ids.append(session.session_id)

        if prev_cursor and prev_cursor in self._session_ids:
            idx = self._session_ids.index(prev_cursor)
            self.move_cursor(row=idx)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        sid = self._selected_session_id()
        if sid:
            self.post_message(SessionSelected(sid))

    def _selected_session_id(self) -> str | None:
        if not self._session_ids:
            return None
        row = self.cursor_row
        if 0 <= row < len(self._session_ids):
            return self._session_ids[row]
        return None

    def select_session(self, session_id: str) -> None:
        if session_id in self._session_ids:
            idx = self._session_ids.index(session_id)
            self.move_cursor(row=idx)


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "\u2026"
