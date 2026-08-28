from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AgentContext:
    workspace_path: str
