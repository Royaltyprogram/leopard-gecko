from leopard_gecko.orchestrator.pipeline import PollRunsResult
from leopard_gecko.orchestrator.worker_loop import run_worker_loop


class FakeOrchestrator:
    def __init__(self, *results: PollRunsResult) -> None:
        self.results = list(results)
        self.poll_calls = 0

    def poll_runs(self) -> PollRunsResult:
        self.poll_calls += 1
        if not self.results:
            return PollRunsResult()
        index = min(self.poll_calls - 1, len(self.results) - 1)
        return self.results[index]


def test_run_worker_loop_once_polls_once() -> None:
    seen: list[PollRunsResult] = []
    orchestrator = FakeOrchestrator(PollRunsResult(running=1, completed=2, failed=3, dispatched=4))

    exit_code = run_worker_loop(
        orchestrator,
        interval_sec=1.0,
        once=True,
        on_iteration=seen.append,
        install_signal_handlers=False,
    )

    assert exit_code == 0
    assert orchestrator.poll_calls == 1
    assert seen == [PollRunsResult(running=1, completed=2, failed=3, dispatched=4)]


def test_run_worker_loop_stops_cleanly_on_keyboard_interrupt() -> None:
    seen: list[PollRunsResult] = []
    sleep_calls: list[float] = []
    orchestrator = FakeOrchestrator(PollRunsResult(running=1))

    def fake_sleep(interval_sec: float) -> None:
        sleep_calls.append(interval_sec)
        raise KeyboardInterrupt

    exit_code = run_worker_loop(
        orchestrator,
        interval_sec=2.5,
        once=False,
        on_iteration=seen.append,
        sleep_fn=fake_sleep,
        install_signal_handlers=False,
    )

    assert exit_code == 0
    assert orchestrator.poll_calls == 1
    assert sleep_calls == [2.5]
    assert seen == [PollRunsResult(running=1)]
