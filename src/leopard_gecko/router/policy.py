import re
from enum import StrEnum

from pydantic import BaseModel

from leopard_gecko.models.config import AppConfig
from leopard_gecko.models.session import Session, SessionStatus, SessionsState
from leopard_gecko.models.task import Task


TOKEN_PATTERN = re.compile(r"[a-z0-9_]{3,}", re.IGNORECASE)


class RouteAction(StrEnum):
    ASSIGN_EXISTING = "assign_existing"
    CREATE_NEW_SESSION = "create_new_session"
    ENQUEUE_GLOBAL = "enqueue_global"


class RouteDecision(BaseModel):
    action: RouteAction
    session_id: str | None = None
    reason: str


def decide_route(task: Task, config: AppConfig, state: SessionsState) -> RouteDecision:
    queue_limit = config.queue_policy.max_queue_per_session
    candidate_scores: list[tuple[int, int, int, Session]] = []

    for session in state.sessions:
        if session.status is SessionStatus.DEAD:
            continue
        if len(session.queue) >= queue_limit:
            continue
        score = _score_session(task, session)
        if score <= 0:
            continue
        idle_rank = 1 if session.status is SessionStatus.IDLE and session.current_task_id is None else 0
        candidate_scores.append((score, idle_rank, -len(session.queue), session))

    if candidate_scores:
        candidate_scores.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
        best_score, _, _, best_session = candidate_scores[0]
        return RouteDecision(
            action=RouteAction.ASSIGN_EXISTING,
            session_id=best_session.session_id,
            reason=f"Matched existing session from prompt/history overlap score={best_score}.",
        )

    live_sessions = [session for session in state.sessions if session.status is not SessionStatus.DEAD]
    if len(live_sessions) < config.max_terminal_num:
        return RouteDecision(
            action=RouteAction.CREATE_NEW_SESSION,
            reason="No compatible session found and terminal capacity is available.",
        )

    return RouteDecision(
        action=RouteAction.ENQUEUE_GLOBAL,
        reason="No compatible session found and terminal capacity is exhausted.",
    )


def _score_session(task: Task, session: Session) -> int:
    current_tokens = _tokenize(f"{task.user_prompt} {task.task_note}")
    if not current_tokens:
        return 0

    history_window = session.task_history[-5:]
    historical_text = " ".join(
        f"{entry.user_prompt} {entry.task_note}"
        for entry in history_window
    )
    history_tokens = _tokenize(historical_text)
    return len(current_tokens & history_tokens)


def _tokenize(text: str) -> set[str]:
    return {match.group(0).lower() for match in TOKEN_PATTERN.finditer(text)}

