"""Routing interfaces and implementations."""

from leopard_gecko.router.agent import AgentRouter
from leopard_gecko.router.factory import build_router
from leopard_gecko.router.policy import (
    ContextRouter,
    RouteAction,
    RouteDecision,
    RoutingError,
    SessionSnapshot,
    build_session_snapshots,
)
from leopard_gecko.router.task_notes import AgentTaskNoteGenerator, TaskNotePort, TemplateTaskNoteGenerator

__all__ = [
    "AgentRouter",
    "AgentTaskNoteGenerator",
    "ContextRouter",
    "RouteAction",
    "RouteDecision",
    "RoutingError",
    "SessionSnapshot",
    "TaskNotePort",
    "TemplateTaskNoteGenerator",
    "build_router",
    "build_session_snapshots",
]
