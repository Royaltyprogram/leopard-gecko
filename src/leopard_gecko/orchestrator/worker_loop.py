import signal
import time
from collections.abc import Callable
from types import FrameType
from typing import Protocol

from leopard_gecko.orchestrator.pipeline import PollRunsResult


class PollingOrchestrator(Protocol):
    def poll_runs(self) -> PollRunsResult:
        """Advance worker-run state once."""


def run_worker_loop(
    orchestrator: PollingOrchestrator,
    interval_sec: float,
    once: bool = False,
    *,
    on_iteration: Callable[[PollRunsResult], None] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    install_signal_handlers: bool = True,
) -> int:
    if interval_sec <= 0:
        raise ValueError("interval_sec must be positive")

    stop_requested = False
    previous_handlers: dict[signal.Signals, signal.Handlers] = {}

    def request_stop(signum: int, frame: FrameType | None) -> None:
        del signum, frame
        nonlocal stop_requested
        stop_requested = True

    if install_signal_handlers:
        for sig in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[sig] = signal.getsignal(sig)
            signal.signal(sig, request_stop)

    try:
        while True:
            result = orchestrator.poll_runs()
            if on_iteration is not None:
                on_iteration(result)

            if once or stop_requested:
                return 0

            try:
                sleep_fn(interval_sec)
            except KeyboardInterrupt:
                return 0

            if stop_requested:
                return 0
    finally:
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)
