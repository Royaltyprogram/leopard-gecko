from rich.console import Console
from rich.table import Table
import typer

from leopard_gecko.orchestrator.pipeline import Orchestrator


app = typer.Typer(help="Leopard Gecko CLI")
console = Console()


@app.command()
def init(data_dir: str | None = typer.Option(None, help="Override data directory")) -> None:
    orchestrator = Orchestrator(data_dir=data_dir)
    config = orchestrator.init_storage()
    console.print(f"Initialized Leopard Gecko at [bold]{orchestrator.paths.root_dir}[/bold]")
    console.print(
        f"max_terminal_num={config.max_terminal_num} "
        f"max_queue_per_session={config.queue_policy.max_queue_per_session}"
    )


@app.command()
def submit(
    user_prompt: str = typer.Argument(..., help="Raw user prompt to route"),
    data_dir: str | None = typer.Option(None, help="Override data directory"),
) -> None:
    orchestrator = Orchestrator(data_dir=data_dir)
    result = orchestrator.submit(user_prompt)
    console.print(f"task_id={result.task_id}")
    console.print(f"decision={result.routing_decision}")
    console.print(f"queue_status={result.queue_status}")
    if result.assigned_session_id:
        console.print(f"assigned_session_id={result.assigned_session_id}")


@app.command()
def status(data_dir: str | None = typer.Option(None, help="Override data directory")) -> None:
    orchestrator = Orchestrator(data_dir=data_dir)
    orchestrator.init_storage()
    state = orchestrator.load_sessions()
    sessions = state.sessions

    busy_count = sum(1 for session in sessions if session.status == "busy")
    idle_count = sum(1 for session in sessions if session.status == "idle")
    blocked_count = sum(1 for session in sessions if session.status == "blocked")
    dead_count = sum(1 for session in sessions if session.status == "dead")

    console.print(f"data_dir={orchestrator.paths.root_dir}")
    console.print(f"sessions={len(sessions)} busy={busy_count} idle={idle_count}")
    console.print(f"blocked={blocked_count} dead={dead_count}")
    console.print(f"global_queue={len(state.global_queue)}")


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


if __name__ == "__main__":
    app()
