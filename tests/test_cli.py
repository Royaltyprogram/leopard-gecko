from typer.testing import CliRunner

from leopard_gecko.cli.main import app
from leopard_gecko.orchestrator.pipeline import PollRunsResult


runner = CliRunner()


def test_poll_command_calls_orchestrator(monkeypatch, tmp_path) -> None:
    seen: dict[str, str | None | bool] = {"poll_called": False, "data_dir": None}

    class FakeOrchestrator:
        def __init__(self, *, data_dir: str | None = None, **kwargs) -> None:
            del kwargs
            seen["data_dir"] = data_dir

        def poll_runs(self) -> PollRunsResult:
            seen["poll_called"] = True
            return PollRunsResult(running=1, completed=2, failed=3, dispatched=4)

    monkeypatch.setattr("leopard_gecko.cli.main.Orchestrator", FakeOrchestrator)

    result = runner.invoke(app, ["poll", "--data-dir", str(tmp_path)])

    assert result.exit_code == 0
    assert seen == {"poll_called": True, "data_dir": str(tmp_path)}
    assert "running=1" in result.stdout
    assert "completed=2" in result.stdout
    assert "failed=3" in result.stdout
    assert "dispatched=4" in result.stdout


def test_worker_command_once_calls_worker_loop(monkeypatch, tmp_path) -> None:
    seen: dict[str, object] = {
        "data_dir": None,
        "interval_sec": None,
        "once": None,
        "loop_called": False,
    }

    class FakeOrchestrator:
        def __init__(self, *, data_dir: str | None = None, **kwargs) -> None:
            del kwargs
            seen["data_dir"] = data_dir

    def fake_run_worker_loop(
        orchestrator,
        interval_sec: float,
        once: bool = False,
        *,
        on_iteration=None,
        **kwargs,
    ) -> int:
        del kwargs
        seen["loop_called"] = True
        seen["interval_sec"] = interval_sec
        seen["once"] = once
        assert isinstance(orchestrator, FakeOrchestrator)
        assert on_iteration is not None
        on_iteration(PollRunsResult(running=0, completed=1, failed=0, dispatched=1))
        return 0

    monkeypatch.setattr("leopard_gecko.cli.main.Orchestrator", FakeOrchestrator)
    monkeypatch.setattr("leopard_gecko.cli.main.run_worker_loop", fake_run_worker_loop)

    result = runner.invoke(
        app,
        ["worker", "--data-dir", str(tmp_path), "--interval-sec", "2.5", "--once"],
    )

    assert result.exit_code == 0
    assert seen == {
        "data_dir": str(tmp_path),
        "interval_sec": 2.5,
        "once": True,
        "loop_called": True,
    }
    assert "running=0" in result.stdout
    assert "completed=1" in result.stdout
    assert "failed=0" in result.stdout
    assert "dispatched=1" in result.stdout
