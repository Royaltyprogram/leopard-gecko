from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, Static

from leopard_gecko.orchestrator.pipeline import SubmissionResult
from leopard_gecko.router.policy import RoutingError
from leopard_gecko.tui.widgets.routing_viz import RoutingVisualizer
from leopard_gecko.tui.widgets.status_bar import StatusBar

if TYPE_CHECKING:
    from leopard_gecko.tui.app import LeopardGeckoApp


class SubmitScreen(Screen):
    def compose(self) -> ComposeResult:
        yield Header()
        yield RoutingVisualizer(id="routing-viz")
        yield Static(id="submit-hint")
        with Horizontal(id="submit-input-area"):
            yield Input(
                placeholder="Enter prompt",
                id="submit-field",
            )
            yield Button("Submit", id="submit-btn", variant="primary")
        yield StatusBar("Ready")
        yield Footer()

    def on_mount(self) -> None:
        # Initialize visualizer with current state
        app: LeopardGeckoApp = self.app  # type: ignore[assignment]
        viz = self.query_one("#routing-viz", RoutingVisualizer)
        max_sessions = self._load_max_sessions()
        if app.current_state:
            viz.update_sessions(app.current_state, max_sessions)
        else:
            from leopard_gecko.models.session import SessionsState

            viz.update_sessions(SessionsState(sessions=[], global_queue=[]), max_sessions)
        self._refresh_input_mode("")

    def on_screen_resume(self) -> None:
        field = self.query_one("#submit-field", Input)
        field.focus()
        self._refresh_input_mode(field.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._do_submit()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "submit-field":
            self._refresh_input_mode(event.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "submit-btn":
            self._do_submit()

    def _do_submit(self) -> None:
        inp = self.query_one("#submit-field", Input)
        text = inp.value.strip()
        if not text:
            return
        inp.value = ""
        if text.startswith("/"):
            self._run_slash_command(text)
            self._refresh_input_mode("")
            return

        viz = self.query_one("#routing-viz", RoutingVisualizer)
        viz.add_log(f"[cyan]Submitting:[/] {text}")
        viz.start_routing(text)

        asyncio.create_task(self._submit_async(text))

    def _run_slash_command(self, text: str) -> None:
        command = text.lower()
        if command in {"/detail", "/session"}:
            self.app.switch_screen("detail")
            return
        if command == "/config":
            self.app.switch_screen("config")
            return

        self.notify(f"Unknown command: {text}", severity="warning")

    def _refresh_input_mode(self, text: str) -> None:
        hint = self.query_one("#submit-hint", Static)
        button = self.query_one("#submit-btn", Button)

        if text.startswith("/"):
            command = text.lower()
            button.label = "Run"
            if command in {"/detail", "/session"}:
                hint.update("[yellow]Command mode[/] Open the current session detail screen.")
            elif command == "/config":
                hint.update("[yellow]Command mode[/] Open the config editor.")
            else:
                hint.update(
                    "[red]Unknown command[/] Available commands: [bold]/detail[/], [bold]/session[/], [bold]/config[/]"
                )
            return

        button.label = "Submit"
        hint.update(
            "[dim]Prompt mode[/] Submit a task normally. Slash commands: [bold]/detail[/], [bold]/session[/], [bold]/config[/]"
        )

    async def _submit_async(self, prompt: str) -> None:
        app: LeopardGeckoApp = self.app  # type: ignore[assignment]
        viz = self.query_one("#routing-viz", RoutingVisualizer)

        if not app.orchestrator:
            viz.add_log("[red]Orchestrator not ready[/]")
            return

        loop = asyncio.get_event_loop()
        try:
            result: SubmissionResult = await loop.run_in_executor(
                None, app.orchestrator.submit, prompt
            )
        except RoutingError as exc:
            viz.add_log(f"[red]Routing error:[/] {exc}")
            return
        except Exception as exc:
            viz.add_log(f"[red]Error:[/] {exc}")
            return

        decision = result.routing_decision.value
        sid = result.assigned_session_id or "-"
        viz.add_log(
            f"[green]OK[/] {result.task_id}: [yellow]{decision}[/] \u2192 {sid}"
        )

        if result.assigned_session_id:
            app.selected_session_id = result.assigned_session_id
        app.selected_task_id = result.task_id

        # Immediately update state so Detail screen sees the new session
        state = await loop.run_in_executor(None, app.orchestrator.load_sessions)
        app.current_state = state

        # Show routing result in visualizer
        max_sessions = self._load_max_sessions()
        viz.update_sessions(state, max_sessions)
        viz.show_result(result)

        if app.poll_manager:
            await app.poll_manager.force_refresh()

    def refresh_state(self) -> None:
        app: LeopardGeckoApp = self.app  # type: ignore[assignment]
        if app.current_state:
            self.query_one(StatusBar).update_from_state(app.current_state)
            viz = self.query_one("#routing-viz", RoutingVisualizer)
            if viz.phase == "idle":
                viz.update_sessions(app.current_state, self._load_max_sessions())

    def _load_max_sessions(self) -> int:
        app: LeopardGeckoApp = self.app  # type: ignore[assignment]
        if not app.orchestrator:
            return 4
        try:
            return app.orchestrator.load_config().max_terminal_num
        except Exception:
            return 4
