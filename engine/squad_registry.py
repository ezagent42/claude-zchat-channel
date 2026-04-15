"""SquadRegistry — Agent-Operator 分队管理 (spec §3)

一个 Operator 负责多个 Agent 的旁听/接管；一个 Agent 只属于一个 Operator 分队。
"""

from __future__ import annotations


class SquadRegistry:
    def __init__(self):
        self._agent_to_operator: dict[str, str] = {}
        self._operator_to_agents: dict[str, list[str]] = {}

    def assign(self, agent_id: str, operator_id: str) -> None:
        current = self._agent_to_operator.get(agent_id)
        if current == operator_id:
            return
        if current is not None:
            self._detach(agent_id, current)
        self._agent_to_operator[agent_id] = operator_id
        self._operator_to_agents.setdefault(operator_id, []).append(agent_id)

    def reassign(self, agent_id: str, new_operator_id: str) -> None:
        self.assign(agent_id, new_operator_id)

    def unassign(self, agent_id: str) -> None:
        current = self._agent_to_operator.pop(agent_id, None)
        if current is not None:
            self._detach(agent_id, current)

    def get_operator(self, agent_id: str) -> str | None:
        return self._agent_to_operator.get(agent_id)

    def get_squad(self, operator_id: str) -> list[str]:
        return list(self._operator_to_agents.get(operator_id, []))

    def list_all(self) -> dict[str, list[str]]:
        """返回所有分队的快照副本：{operator_id: [agent_ids]}。"""
        return {op: list(agents) for op, agents in self._operator_to_agents.items()}

    def _detach(self, agent_id: str, operator_id: str) -> None:
        squad = self._operator_to_agents.get(operator_id)
        if not squad:
            return
        squad[:] = [a for a in squad if a != agent_id]
        if not squad:
            self._operator_to_agents.pop(operator_id, None)
