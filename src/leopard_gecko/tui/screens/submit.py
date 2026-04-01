from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, RichLog

from leopard_gecko.orchestrator.pipeline import SubmissionResult
from leopard_gecko.router.policy import RoutingError
from leopard_gecko.tui.widgets.status_bar import StatusBar

if TYPE_CHECKING:
    from leopard_gecko.tui.app import LeopardGeckoApp


class SubmitScreen(Screen):
    def compose(self) -> ComposeResult:
        yield Header()
        yield RichLog(highlight=True, markup=True, id="submit-log")
        with Horizontal(id="submit-input-area"):
            yield Input(placeholder="Enter prompt...", id="submit-field")
            yield Button("Submit", id="submit-btn", variant="primary")
        yield StatusBar("Ready")
        yield Footer()

    def on_mount(self) -> None:
        log = self.query_one("#submit-log", RichLog)
        log.write("[bold]Recent Submissions[/bold]")
        log.write("[dim]No submissions yet.[/dim]")

    def on_screen_resume(self) -> None:
        self.query_one("#submit-field", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._do_submit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "submit-btn":
            self._do_submit()

    def _do_submit(self) -> None:
        inp = self.query_one("#submit-field", Input)
        text = inp.value.strip()
        if not text:
            return
        inp.value = ""

        log = self.query_one("#submit-log", RichLog)
        log.write(f"\n[cyan]Submitting:[/cyan] {text}")

        asyncio.create_task(self._submit_async(text))

    async def _submit_async(self, prompt: str) -> None:
        app: LeopardGeckoApp = self.app  # type: ignore[assignment]
        log = self.query_one("#submit-log", RichLog)

        if not app.orchestrator:
            log.write("[red]Orchestrator not ready[/red]")
            return

        loop = asyncio.get_event_loop()
        try:
            result: SubmissionResult = await loop.run_in_executor(
                None, app.orchestrator.submit, prompt
            )
        except RoutingError as exc:
            log.write(f"[red]Routing error:[/red] {exc}")
            return
        except Exception as exc:
            log.write(f"[red]Error:[/red] {exc}")
            return

        decision = result.routing_decision.value
        sid = result.assigned_session_id or "-"
        log.write(f"[green]OK[/green] {result.task_id}: [yellow]{decision}[/yellow] → {sid}")

        if result.assigned_session_id:
            app.selected_session_id = result.assigned_session_id

        # Immediately update state so Detail screen sees the new session
        state = await loop.run_in_executor(None, app.orchestrator.load_sessions)
        app.current_state = state

        if app.poll_manager:
            await app.poll_manager.force_refresh()

    def refresh_state(self) -> None:
        app: LeopardGeckoApp = self.app  # type: ignore[assignment]
        if app.current_state:
            self.query_one(StatusBar).update_from_state(app.current_state)
