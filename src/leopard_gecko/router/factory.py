from leopard_gecko.models.config import AppConfig, RouterBackend
from leopard_gecko.router.agent import AgentRouter
from leopard_gecko.router.policy import ContextRouter


def build_router(config: AppConfig) -> ContextRouter:
    if config.router.backend is RouterBackend.AGENT:
        return AgentRouter(config.router.agent)

    raise ValueError(f"Unsupported router backend: {config.router.backend}")
