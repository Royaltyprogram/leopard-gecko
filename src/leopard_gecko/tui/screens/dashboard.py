from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Header

from leopard_gecko.tui.widgets.global_queue import GlobalQueuePanel
from leopard_gecko.tui.widgets.session_table import SessionSelected, SessionTable
from leopard_gecko.tui.widgets.status_bar import StatusBar

if TYPE_CHECKING:
    from leopard_gecko.tui.app import LeopardGeckoApp


class DashboardScreen(Screen):
    BINDINGS = [
        Binding("s", "go_submit", "Submit", key_display="s"),
        Binding("d", "go_detail", "Detail", key_display="d"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield SessionTable(id="dashboard-sessions")
        yield GlobalQueuePanel(id="dashboard-gq")
        yield StatusBar("Loading...")
        yield Footer()

    def on_mount(self) -> None:
        app: LeopardGeckoApp = self.app  # type: ignore[assignment]
        if app.current_state:
            self._refresh(app)

    def on_screen_resume(self) -> None:
        app: LeopardGeckoApp = self.app  # type: ignore[assignment]
        if app.current_state:
            self._refresh(app)

    def refresh_state(self) -> None:
        app: LeopardGeckoApp = self.app  # type: ignore[assignment]
        if app.current_state:
            self._refresh(app)

    def _refresh(self, app: LeopardGeckoApp) -> None:
        state = app.current_state
        self.query_one(SessionTable).refresh_from_state(state)
        self.query_one(GlobalQueuePanel).update_from_state(state)
        self.query_one(StatusBar).update_from_state(state)

    def on_session_selected(self, event: SessionSelected) -> None:
        app: LeopardGeckoApp = self.app  # type: ignore[assignment]
        app.selected_session_id = event.session_id

    def on_data_table_row_selected(self, event) -> None:
        app: LeopardGeckoApp = self.app  # type: ignore[assignment]
        if app.selected_session_id:
            app.switch_screen("detail")

    def action_go_submit(self) -> None:
        self.app.switch_screen("submit")

    def action_go_detail(self) -> None:
        app: LeopardGeckoApp = self.app  # type: ignore[assignment]
        if app.selected_session_id:
            app.switch_screen("detail")
        else:
            self.notify("Select a session first", severity="warning")
