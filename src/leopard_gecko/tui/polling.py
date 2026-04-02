from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from textual.message import Message
from textual.timer import Timer

if TYPE_CHECKING:
    from textual.app import App

    from leopard_gecko.models.session import SessionsState
    from leopard_gecko.orchestrator.pipeline import Orchestrator, PollRunsResult


class PollCompleted(Message):
    def __init__(self, result: PollRunsResult, state: SessionsState) -> None:
        super().__init__()
        self.result = result
        self.state = state


class TUIPollManager:
    def __init__(self, app: App, orchestrator: Orchestrator, interval: float) -> None:
        self._app = app
        self._orchestrator = orchestrator
        self._interval = interval
        self._timer: Timer | None = None
        self._polling = False

    def start(self) -> None:
        self._timer = self._app.set_interval(self._interval, self._poll_tick)

    def stop(self) -> None:
        if self._timer:
            self._timer.stop()
            self._timer = None

    async def _poll_tick(self) -> None:
        if self._polling:
            return
        self._polling = True
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, self._orchestrator.poll_runs)
            state = await loop.run_in_executor(None, self._orchestrator.load_sessions)
            self._app.post_message(PollCompleted(result=result, state=state))
        except Exception:
            pass
        finally:
            self._polling = False

    async def force_refresh(self) -> None:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, self._orchestrator.poll_runs)
        state = await loop.run_in_executor(None, self._orchestrator.load_sessions)
        self._app.post_message(PollCompleted(result=result, state=state))
