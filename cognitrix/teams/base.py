import asyncio
import logging
from functools import cached_property
from typing import TYPE_CHECKING, Any, Optional

from odbms import Model
from rich import print

from cognitrix.agents.base import Agent, Message, MessagePriority
from cognitrix.tasks.base import Task, TaskStatus

if TYPE_CHECKING:
    from cognitrix.sessions.base import Session

logger = logging.getLogger('cognitrix.log')

class Team(Model):
    name: str
    """Name of the team"""

    description: str
    """Description of the team"""

    assigned_agents: list[str] = []
    """List of agent IDs in the team"""

    tasks: list[Task] = []
    """List of tasks in the team"""

    leader_id: str | None = None
    """ID of the team leader"""

    class Config:
        arbitrary_types_allowed = True

    @cached_property
    async def agents(self) -> list[Agent]:
        return [agent for aid in self.assigned_agents if (agent := await Agent.get(aid)) is not None]

    @property
    async def leader(self) -> Agent | None:
        leader = await Agent.get(self.leader_id) if self.leader_id else None
        if leader:
            from cognitrix.providers.base import LLM
            new_llm = LLM.load_llm(leader.llm.provider)
            if new_llm:
                new_llm.temperature = leader.llm.temperature
                leader.llm = new_llm
        return leader

    @leader.setter
    async def leader(self, agent: Agent):
        if agent.id in self.assigned_agents:
            self.leader_id = agent.id
        else:
            raise ValueError("The leader must be a member of the team.")

    async def add_agent(self, agent: Agent):
        if agent.id not in self.assigned_agents:
            self.assigned_agents.append(agent.id)

    async def remove_agent(self, agent_id: str):
        self.assigned_agents = [aid for aid in self.assigned_agents if aid != agent_id]

    async def broadcast_message(self, sender: Agent, content: str, priority: MessagePriority = MessagePriority.NORMAL):
        for agent in await self.agents:
            if agent.id != sender.id:
                message = Message(sender=sender.name, receiver=agent.name, content=content, priority=priority)
                await agent.receive_message(message)

    async def send_message(self, sender: Agent, receiver: Agent, content: str, priority: MessagePriority = MessagePriority.NORMAL, session: Optional['Session'] = None):
        message = Message(sender=sender.name, receiver=receiver.name, content=content, priority=priority)
        await receiver.receive_message(message, session)

    async def start_communication(self):
        while True:
            for agent in await self.agents:
                while agent.response_list:
                    message, response = agent.response_list.pop(0)
                    processed_response = await self.process_agent_message(agent, message, response.result)
                    print(f"{agent.name} processed message from {message.sender}: {processed_response}")
            await asyncio.sleep(0.1)  # Small delay to prevent busy-waiting

    async def create_task(self, title: str, description: str) -> Task:
        task = Task(title=title, description=description, assigned_agents=self.assigned_agents, team_id=self.id)
        await task.save()
        self.tasks.append(task)
        return task

    async def assign_task(self, task_id: str):
        check_assigned = next((t for t in self.tasks if t.id == task_id), None)
        if check_assigned:
            return check_assigned
        task = await Task.get(task_id)
        if task:
            task.assigned_agents = self.assigned_agents
            task.status = TaskStatus.IN_PROGRESS
            task.team_id = self.id
            await task.save()
            self.tasks.append(task)
            return task
        return None

    def get_task_status(self, task_id: str) -> TaskStatus | None:
        task = next((t for t in self.tasks if t.id == task_id), None)
        return task.status if task else None

    def get_task_results(self, task_id: str) -> list[str] | None:
        task = next((t for t in self.tasks if t.id == task_id), None)
        return task.results if task else None

    async def process_agent_message(self, agent: Agent, message: Message, response: str):
        return await TeamManager.process_agent_message(self, agent, message, response)

    async def get_assigned_tasks(self):
        return [task for task in self.tasks if task.team_id == self.id]

    @classmethod
    async def get_session(cls, team_id: str) -> 'Session':
        from cognitrix.sessions.base import Session

        session = await Session.get_by_team_id(team_id)
        if not session:
            session = Session(team_id=team_id)
            await session.save()

        return session

# ---------------------------------------------------------------------------
# Attach TeamManager helpers to the Team model for centralised management and
# backward-compatibility with any legacy call-sites.
# ---------------------------------------------------------------------------

# Instance-level manager
class TeamManager:
    """
    Manager class for Team-related business logic.
    This class separates business logic from the data model,
    making the code more maintainable and testable.
    """

    def __init__(self):
        self.teams: dict[str, Team] = {}
        # Strong refs so fire-and-forget team tasks aren't GC'd mid-run.
        self._bg_tasks: set[asyncio.Task] = set()

    def create_team(self, name: str, description: str) -> Team:
        """Create a new team"""
        team = Team(name=name, description=description)
        self.teams[team.id] = team
        return team

    def get_team(self, team_id: str) -> Team | None:
        """Get a team by ID"""
        return self.teams.get(team_id)

    def delete_team(self, team_id: str):
        """Delete a team"""
        if team_id in self.teams:
            del self.teams[team_id]

    async def start_all_teams(self):
        """Start communication for all teams"""
        for team in self.teams.values():
            task = asyncio.create_task(team.start_communication())
            self._bg_tasks.add(task)
            task.add_done_callback(self._bg_tasks.discard)

    async def send_cross_team_message(self, sender_team: Team, sender_agent: Agent,
                                  receiver_team: Team, receiver_agent: Agent,
                                  content: str, priority: MessagePriority = MessagePriority.NORMAL):
        """Send a message between teams"""
        message = Message(
            sender=f"{sender_team.name}:{sender_agent.name}",
            receiver=f"{receiver_team.name}:{receiver_agent.name}",
            content=content,
            priority=priority
        )
        await receiver_agent.receive_message(message)

    @staticmethod
    async def process_agent_message(team: Team, agent: Agent, message: Message, response: str):
        """Process a message from an agent"""
        # Implementation would go here - simplified for now
        return f"Processed message from {agent.name}: {response[:50]}..."

    # Convenience alias so callers can do `TeamManager.create(...)`
    @staticmethod
    async def create(name: str, description: str) -> Team:  # type: ignore[override]
        manager = TeamManager()
        return manager.create_team(name, description)


def _team_manager(self: 'Team') -> 'TeamManager':
    return TeamManager()

Team.manager = property(_team_manager)  # type: ignore[attr-defined]

# Class-level helpers
Team.create_team = staticmethod(TeamManager.create_team)  # type: ignore[attr-defined]
Team.get_team = staticmethod(TeamManager.get_team)  # type: ignore[attr-defined]
Team.delete_team = staticmethod(TeamManager.delete_team)  # type: ignore[attr-defined]
