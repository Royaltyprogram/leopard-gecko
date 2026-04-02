from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
import typer

load_dotenv()

from leopard_gecko.models.session import SessionStatus
from leopard_gecko.models.config import WorkerBackend
from leopard_gecko.orchestrator.pipeline import Orchestrator, PollRunsResult
from leopard_gecko.orchestrator.worker_loop import run_worker_loop
from leopard_gecko.router.policy import RoutingError


app = typer.Typer(help="Leopard Gecko CLI")
console = Console()


def _print_poll_result(result: PollRunsResult) -> None:
    console.print(f"running={result.running}")
    console.print(f"completed={result.completed}")
    console.print(f"failed={result.failed}")
    console.print(f"dispatched={result.dispatched}")


@app.command()
def init(
    data_dir: str | None = typer.Option(None, help="Override data directory"),
    worker_backend: WorkerBackend = typer.Option(
        WorkerBackend.CODEX,
        "--worker-backend",
        help="Default worker backend to store in config",
    ),
) -> None:
    orchestrator = Orchestrator(data_dir=data_dir, worker_backend=worker_backend)
    config = orchestrator.init_storage()
    if config.worker.backend is not worker_backend:
        config = config.model_copy(update={"worker": config.worker.model_copy(update={"backend": worker_backend})})
        orchestrator.config_repo.save(config)
    console.print(f"Initialized Leopard Gecko at [bold]{orchestrator.paths.root_dir}[/bold]")
    console.print(
        f"router_backend={config.router.backend} "
        f"worker_backend={config.worker.backend} "
        f"max_terminal_num={config.max_terminal_num} "
        f"max_queue_per_session={config.queue_policy.max_queue_per_session}"
    )


@app.command()
def submit(
    user_prompt: str = typer.Argument(..., help="Raw user prompt to route"),
    data_dir: str | None = typer.Option(None, help="Override data directory"),
    worker_backend: WorkerBackend | None = typer.Option(
        None,
        "--worker-backend",
        help="Override worker backend for this submission only",
    ),
) -> None:
    orchestrator = Orchestrator(data_dir=data_dir, worker_backend=worker_backend)
    try:
        result = orchestrator.submit(user_prompt)
    except RoutingError as exc:
        console.print(f"routing_error={exc}")
        raise typer.Exit(code=1) from exc
    console.print(f"task_id={result.task_id}")
    console.print(f"decision={result.routing_decision}")
    console.print(f"queue_status={result.queue_status}")
    if result.assigned_session_id:
        console.print(f"assigned_session_id={result.assigned_session_id}")


@app.command()
def status(data_dir: str | None = typer.Option(None, help="Override data directory")) -> None:
    orchestrator = Orchestrator(data_dir=data_dir)
    config = orchestrator.init_storage()
    state = orchestrator.load_sessions()
    sessions = state.sessions

    busy_count = sum(1 for session in sessions if session.status is SessionStatus.BUSY)
    idle_count = sum(1 for session in sessions if session.status is SessionStatus.IDLE)
    blocked_count = sum(1 for session in sessions if session.status is SessionStatus.BLOCKED)
    dead_count = sum(1 for session in sessions if session.status is SessionStatus.DEAD)

    console.print(f"data_dir={orchestrator.paths.root_dir}")
    console.print(f"router_backend={config.router.backend}")
    console.print(f"worker_backend={config.worker.backend}")
    console.print(f"sessions={len(sessions)} busy={busy_count} idle={idle_count}")
    console.print(f"blocked={blocked_count} dead={dead_count}")
    console.print(f"global_queue={len(state.global_queue)}")


@app.command()
def poll(data_dir: str | None = typer.Option(None, help="Override data directory")) -> None:
    orchestrator = Orchestrator(data_dir=data_dir)
    result = orchestrator.poll_runs()
    _print_poll_result(result)


@app.command()
def worker(
    data_dir: str | None = typer.Option(None, help="Override data directory"),
    interval_sec: float = typer.Option(2.0, "--interval-sec", min=0.1, help="Polling interval"),
    once: bool = typer.Option(False, "--once", help="Run a single poll iteration and exit"),
) -> None:
    orchestrator = Orchestrator(data_dir=data_dir)
    exit_code = run_worker_loop(
        orchestrator,
        interval_sec=interval_sec,
        once=once,
        on_iteration=_print_poll_result,
    )
    if exit_code:
        raise typer.Exit(code=exit_code)


@app.command()
def sessions(data_dir: str | None = typer.Option(None, help="Override data directory")) -> None:
    orchestrator = Orchestrator(data_dir=data_dir)
    orchestrator.init_storage()
    state = orchestrator.load_sessions()

    table = Table(title="Sessions")
    table.add_column("session_id")
    table.add_column("status")
    table.add_column("current_task_id")
    table.add_column("queue")
    table.add_column("history")
    table.add_column("last_heartbeat")

    for session in state.sessions:
        table.add_row(
            session.session_id,
            session.status,
            session.current_task_id or "-",
            str(len(session.queue)),
            str(len(session.task_history)),
            session.last_heartbeat.isoformat(),
        )

    console.print(table)


@app.command()
def tui(
    data_dir: str | None = typer.Option(None, help="Override data directory"),
    poll_interval: float = typer.Option(2.0, "--poll-interval", min=0.1, help="Polling interval in seconds"),
) -> None:
    from leopard_gecko.tui.app import LeopardGeckoApp

    tui_app = LeopardGeckoApp(data_dir=data_dir, poll_interval=poll_interval)
    tui_app.run()


if __name__ == "__main__":
    app()
