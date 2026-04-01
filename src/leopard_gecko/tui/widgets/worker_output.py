import asyncio
import json
from pathlib import Path

from textual.widgets import RichLog

from leopard_gecko.models.session import SessionsState


class WorkerOutputLog(RichLog):
    _current_session_id: str | None = None
    _tail_task: asyncio.Task | None = None
    _state: SessionsState | None = None

    def update_state(self, state: SessionsState) -> None:
        self._state = state
        if self._current_session_id:
            session = self._find_session(self._current_session_id)
            if session and session.last_run_output_path:
                output_path = Path(session.last_run_output_path)
                if self._tail_task and not self._tail_task.done():
                    return
                self._start_tailing(output_path)

    def watch_session(self, session_id: str) -> None:
        self._stop_tailing()
        self._current_session_id = session_id
        self.clear()

        if not self._state:
            self.write("[dim]No state loaded[/dim]")
            return

        session = self._find_session(session_id)
        if not session:
            self.write(f"[dim]Session {session_id} not found[/dim]")
            return

        if not session.last_run_output_path:
            self.write(f"[dim]No worker output for {session_id}[/dim]")
            return

        output_path = Path(session.last_run_output_path)
        if not output_path.exists():
            self.write(f"[dim]Output file not found: {output_path}[/dim]")
            return

        self._start_tailing(output_path)

    def _start_tailing(self, path: Path) -> None:
        self._stop_tailing()
        self._tail_task = asyncio.create_task(self._tail_file(path))

    def _stop_tailing(self) -> None:
        if self._tail_task and not self._tail_task.done():
            self._tail_task.cancel()
        self._tail_task = None

    async def _tail_file(self, path: Path) -> None:
        offset = 0
        try:
            while True:
                try:
                    size = path.stat().st_size
                except FileNotFoundError:
                    await asyncio.sleep(0.5)
                    continue

                if size > offset:
                    with open(path, "r", encoding="utf-8") as f:
                        f.seek(offset)
                        new_data = f.read()
                        offset = f.tell()

                    for line in new_data.splitlines():
                        line = line.strip()
                        if not line:
                            continue
                        self.write(_format_jsonl_line(line))

                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            pass

    def _find_session(self, session_id: str):
        if not self._state:
            return None
        for s in self._state.sessions:
            if s.session_id == session_id:
                return s
        return None

    def on_unmount(self) -> None:
        self._stop_tailing()


def _format_jsonl_line(line: str) -> str:
    try:
        data = json.loads(line)
        if isinstance(data, dict):
            msg = data.get("message") or data.get("text") or data.get("content")
            if msg:
                return str(msg)
        return line
    except json.JSONDecodeError:
        return line
