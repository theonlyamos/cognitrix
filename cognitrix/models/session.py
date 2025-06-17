from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from odbms import Model

if TYPE_CHECKING:
    from cognitrix.models import Agent, Team

class Session(Model):
    chat: list[dict[str, Any]] = []
    """The chat history of the session"""

    datetime: str = (datetime.now()).strftime("%a %b %d %Y %H:%M:%S")
    """When the session was started"""

    agent_id: str | None = None
    """The id of the agent that started the session"""

    task_id: str | None = None
    """The id of the task that started the session"""

    team_id: str | None = None
    """The id of the team that started the session"""

    started_at: str | None = None
    """Started date of the task"""

    completed_at: str | None = None
    """Completion date of the task"""

    pid: str | None = None
    """Worker Id of task"""

    @classmethod
    async def load(cls, session_id: str) -> Session:
        """Load an existing session or create a new one if it doesn't exist"""
        session = await cls.get(session_id)
        if not session:
            session = cls()
            await session.save()
        return session

    @classmethod
    async def list_sessions(cls) -> list[Session]:
        return await cls.all()

    @classmethod
    async def delete(cls, session_id: str):
        """Delete session by id"""
        return await cls.delete_one({'id': session_id})

    def update_history(self, message: dict[str, Any]):
        self.chat.append(message)

    @property
    async def agent(self) -> Agent | None:
        from cognitrix.models import Agent
        return await Agent.get(self.agent_id) if self.agent_id else None

    @property
    async def team(self) -> Team | None:
        from cognitrix.models import Team
        return await Team.get(self.team_id) if self.team_id else None

    @classmethod
    async def get_by_agent_id(cls, agent_id: str) -> Session:
        """Retrieve a session by agent_id"""
        session = await cls.find_one({'agent_id': agent_id})
        if not session:
            session = cls(agent_id=agent_id)
            await session.save()
        return session

    @classmethod
    async def get_by_task_id(cls, task_id: str) -> list[Session]:
        """Retrieve a session by task_id"""
        return await cls.find({'task_id': task_id})
