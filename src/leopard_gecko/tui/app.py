from __future__ import annotations

from textual.app import App
from textual.binding import Binding

from leopard_gecko.models.session import SessionsState
from leopard_gecko.orchestrator.pipeline import Orchestrator
from leopard_gecko.tui.polling import PollCompleted, TUIPollManager
from leopard_gecko.tui.screens.config import ConfigScreen
from leopard_gecko.tui.screens.detail import DetailScreen
from leopard_gecko.tui.screens.submit import SubmitScreen


class LeopardGeckoApp(App):
    CSS_PATH = "app.tcss"
    TITLE = "Leopard Gecko"

    SCREENS = {
        "submit": SubmitScreen,
        "detail": DetailScreen,
        "config": ConfigScreen,
    }

    BINDINGS = [
        Binding("escape", "go_home", "Back/Quit", key_display="esc", priority=True),
        Binding("f2", "show_config", "Config", priority=True),
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
    ]

    def __init__(
        self,
        *,
        data_dir: str | None = None,
        poll_interval: float = 2.0,
    ) -> None:
        super().__init__()
        self._data_dir = data_dir
        self._poll_interval = poll_interval
        self.orchestrator: Orchestrator | None = None
        self.poll_manager: TUIPollManager | None = None
        self.current_state: SessionsState | None = None
        self.selected_session_id: str | None = None
        self.selected_task_id: str | None = None

    async def on_mount(self) -> None:
        self.orchestrator = Orchestrator(data_dir=self._data_dir)
        self.orchestrator.init_storage()

        self.poll_manager = TUIPollManager(self, self.orchestrator, self._poll_interval)
        self.poll_manager.start()
        await self.poll_manager.force_refresh()

        self.push_screen("submit")

    def on_poll_completed(self, event: PollCompleted) -> None:
        self.current_state = event.state

        screen = self.screen
        if hasattr(screen, "refresh_state"):
            screen.refresh_state()

    def action_go_home(self) -> None:
        if isinstance(self.screen, SubmitScreen):
            self.exit()
            return
        self.switch_screen("submit")

    async def action_refresh(self) -> None:
        if self.poll_manager:
            await self.poll_manager.force_refresh()

    def action_show_config(self) -> None:
        self.switch_screen("config")

    def on_unmount(self) -> None:
        if self.poll_manager:
            self.poll_manager.stop()
