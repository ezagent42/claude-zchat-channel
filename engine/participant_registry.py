"""ParticipantRegistry — IRC nick → Participant 角色映射 (spec §3.6)"""

from __future__ import annotations

from typing import Any

from zchat_protocol.participant import Participant, ParticipantRole


class ParticipantRegistry:
    def __init__(self):
        self._agents: dict[str, Participant] = {}
        self._operators: dict[str, Participant] = {}
        self._bridges: dict[str, str] = {}  # bridge_nick → bridge_type

    def register_agent(
        self, nick: str, metadata: dict[str, Any] | None = None
    ) -> Participant:
        if nick in self._operators or nick in self._bridges:
            raise ValueError(f"nick {nick} 已注册为其他角色")
        if nick in self._agents:
            return self._agents[nick]
        p = Participant(
            id=nick, role=ParticipantRole.AGENT, metadata=metadata or {}
        )
        self._agents[nick] = p
        return p

    def register_operator(
        self, nick: str, metadata: dict[str, Any] | None = None
    ) -> Participant:
        if nick in self._agents or nick in self._bridges:
            raise ValueError(f"nick {nick} 已注册为其他角色")
        if nick in self._operators:
            return self._operators[nick]
        p = Participant(
            id=nick, role=ParticipantRole.OPERATOR, metadata=metadata or {}
        )
        self._operators[nick] = p
        return p

    def register_bridge(self, nick: str, bridge_type: str) -> None:
        if nick in self._agents or nick in self._operators:
            raise ValueError(f"nick {nick} 已注册为其他角色")
        self._bridges[nick] = bridge_type

    def identify(self, nick: str) -> Participant | None:
        if nick in self._agents:
            return self._agents[nick]
        if nick in self._operators:
            return self._operators[nick]
        if nick in self._bridges:
            return Participant(id=nick, role=ParticipantRole.CUSTOMER)
        return None

    def unregister(self, nick: str) -> None:
        self._agents.pop(nick, None)
        self._operators.pop(nick, None)
        self._bridges.pop(nick, None)
