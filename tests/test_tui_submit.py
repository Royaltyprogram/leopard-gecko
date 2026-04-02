from __future__ import annotations

from datetime import datetime, timezone

from leopard_gecko.models.session import Session, SessionsState
from leopard_gecko.models.task import QueueStatus, RoutingDecision, Task
from leopard_gecko.orchestrator.pipeline import SubmissionResult
from leopard_gecko.tui.app import LeopardGeckoApp
from leopard_gecko.tui.screens.detail import DetailScreen
from leopard_gecko.tui.screens.submit import SubmitScreen
import pytest


class _FakeOrchestrator:
    def __init__(self, state: SessionsState, max_terminal_num: int = 4) -> None:
        self._state = state
        self._max_terminal_num = max_terminal_num

    def submit(self, prompt: str) -> SubmissionResult:
        return SubmissionResult(
            task_id="task-1",
            queue_status=QueueStatus.RUNNING,
            routing_decision=RoutingDecision.CREATED_NEW_SESSION,
            assigned_session_id="session-1",
            created_session=True,
            dispatched=True,
        )

    def load_sessions(self) -> SessionsState:
        return self._state

    def load_config(self):
        class _Config:
            max_terminal_num = 4

        config = _Config()
        config.max_terminal_num = self._max_terminal_num
        return config


class _FakePollManager:
    async def force_refresh(self) -> None:
        return None

    def stop(self) -> None:
        return None


@pytest.mark.asyncio
async def test_submit_keeps_user_on_submit_screen_after_assigning_session(tmp_path) -> None:
    app = LeopardGeckoApp(data_dir=str(tmp_path), poll_interval=9999.0)

    async with app.run_test() as pilot:
        await pilot.pause()

        fake_state = SessionsState(sessions=[Session(session_id="session-1")], global_queue=[])
        app.orchestrator = _FakeOrchestrator(fake_state)
        app.poll_manager = _FakePollManager()

        submit_screen = app.screen
        assert isinstance(submit_screen, SubmitScreen)

        submit_screen.query_one("#submit-field").value = "first prompt"
        submit_screen._do_submit()

        for _ in range(20):
            await pilot.pause()
            if app.selected_session_id == "session-1":
                break

        assert app.selected_session_id == "session-1"
        assert isinstance(app.screen, SubmitScreen)


@pytest.mark.asyncio
async def test_detail_command_opens_detail_screen_after_app_restart(tmp_path) -> None:
    app = LeopardGeckoApp(data_dir=str(tmp_path))

    async with app.run_test() as pilot:
        await pilot.pause()

        app.orchestrator.task_repo.save(  # type: ignore[union-attr]
            Task(
                task_id="task-existing",
                user_prompt="existing prompt",
                task_note="existing prompt",
                queue_status=QueueStatus.COMPLETED,
                created_at=datetime.now(timezone.utc),
            )
        )

        submit_screen = app.screen
        assert isinstance(submit_screen, SubmitScreen)
        assert app.selected_session_id is None

        submit_screen.query_one("#submit-field").value = "/detail"
        submit_screen._do_submit()
        await pilot.pause()

        assert isinstance(app.screen, DetailScreen)
        assert app.selected_task_id == "task-existing"
