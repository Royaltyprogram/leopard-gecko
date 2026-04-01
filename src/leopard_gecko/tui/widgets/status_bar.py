from datetime import datetime, timezone

from textual.widgets import Static

from leopard_gecko.models.session import SessionsState, SessionStatus


class StatusBar(Static):
    def update_from_state(self, state: SessionsState, *, poll_info: str = "") -> None:
        sessions = state.sessions
        busy = sum(
            1 for s in sessions if s.status in {SessionStatus.BUSY, SessionStatus.COOLDOWN}
        )
        idle = sum(1 for s in sessions if s.status == SessionStatus.IDLE)
        blocked = sum(1 for s in sessions if s.status == SessionStatus.BLOCKED)
        dead = sum(1 for s in sessions if s.status == SessionStatus.DEAD)
        gq = len(state.global_queue)
        now = datetime.now(timezone.utc).strftime("%H:%M:%S")

        parts = [
            f"Sessions: {len(sessions)}",
            f"[green]{idle} idle[/]",
            f"[yellow]{busy} busy[/]",
        ]
        if blocked:
            parts.append(f"[red]{blocked} blocked[/]")
        if dead:
            parts.append(f"[dim]{dead} dead[/]")
        parts.append(f"GQ: {gq}")
        if poll_info:
            parts.append(poll_info)
        parts.append(now)

        self.update(" | ".join(parts))
