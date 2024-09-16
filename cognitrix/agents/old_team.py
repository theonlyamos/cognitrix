import asyncio
import json
import uuid
import logging
from typing import List, Optional, Self, Dict
from pydantic import BaseModel, Field
from datetime import datetime
import aiofiles

from cognitrix.agents.base import Agent
from cognitrix.config import TEAMS_FILE

logger = logging.getLogger('cognitrix.log')

class Team(BaseModel):
    name: str = Field(default='Team')
    """Name of the team"""
    
    assigned_agents: List[str] = Field(default_factory=list)
    """List of agent IDs in the team"""
    
    team_leader_id: Optional[str] = None
    """ID of the team leader agent"""
    
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    """Unique id for the team"""
    
    description: Optional[str] = Field(default=None)
    """Brief description of the team's purpose or role"""

    created_at: str = Field(default=datetime.now().strftime("%a %b %d %Y %H:%M:%S"))
    """Timestamp of when the team was created"""

    updated_at: str = Field(default=datetime.now().strftime("%a %b %d %Y %H:%M:%S"))
    """Timestamp of when the team was last modified"""

    tags: List[str] = Field(default_factory=list)
    """List of tags or categories for the team"""

    max_size: Optional[int] = Field(default=None)
    """Maximum number of agents allowed in the team (optional)"""
    
    class Config:
        arbitrary_types_allowed = True

    @classmethod
    async def _load_teams_from_file(cls) -> Dict[str, Dict]:
        async with aiofiles.open(TEAMS_FILE, 'r') as file:
            content = await file.read()
            return json.loads(content) if content else {}

    @classmethod
    async def _save_teams_to_file(cls, teams: Dict[str, Dict]):
        async with aiofiles.open(TEAMS_FILE, 'w') as file:
            await file.write(json.dumps(teams, indent=4))

    @classmethod
    async def create_team(cls, name: str = '', assigned_agents: List[str] = []) -> Optional[Self]:
        try:
            name = name or input("\n[Enter team name]: ")
            new_team = cls(name=name, assigned_agents=assigned_agents)
            teams = await cls._load_teams_from_file()
            teams[new_team.id] = new_team.dict()
            await cls._save_teams_to_file(teams)
            return new_team
        except Exception as e:
            logger.error(f"Error creating team: {str(e)}")
            return None

    @classmethod
    async def list_teams(cls) -> List[Self]:
        try:
            teams = await cls._load_teams_from_file()
            return [cls(**team_data) for team_data in teams.values()]
        except Exception as e:
            logger.exception(f"Error listing teams: {str(e)}")
            return []

    @classmethod
    async def get(cls, id_or_name: str) -> Optional[Self]:
        """Get a team by ID or name"""
        teams = await cls._load_teams_from_file()
        team_data = teams.get(id_or_name)
        if team_data:
            return cls(**team_data)
        return next((cls(**data) for data in teams.values() if data['name'].lower() == id_or_name.lower()), None)

    async def save(self):
        """Save current team"""
        self.last_modified = datetime.now().strftime("%a %b %d %Y %H:%M:%S")
        teams = await self._load_teams_from_file()
        teams[self.id] = self.dict()
        await self._save_teams_to_file(teams)
        return self.id

    @classmethod
    async def delete(cls, id_or_name: str) -> bool:
        """Delete team by id or name"""
        teams = await cls._load_teams_from_file()
        if id_or_name in teams:
            del teams[id_or_name]
        else:
            for team_id, team_data in list(teams.items()):
                if team_data['name'].lower() == id_or_name.lower():
                    del teams[team_id]
                    break
        if len(teams) < len(await cls._load_teams_from_file()):
            await cls._save_teams_to_file(teams)
            return True
        return False

    async def agents(self) -> List[Agent]:
        """Load and return the agents associated with this team"""
        return [agent for agent in await Agent.list_agents() if agent.id in self.assigned_agents]
    
    async def team_leader(self) -> Optional[Agent]:
        """Load and return the team leader agent"""
        return await Agent.get(self.team_leader_id)

    def add_agent(self, agent_id: str):
        """Add an agent ID to the team"""
        if self.max_size and len(self.assigned_agents) >= self.max_size:
            raise ValueError(f"Team has reached its maximum size of {self.max_size}")
        if agent_id not in self.assigned_agents:
            self.assigned_agents.append(agent_id)

    def remove_agent(self, agent_id: str):
        """Remove an agent ID from the team"""
        self.assigned_agents = [id for id in self.assigned_agents if id != agent_id]

    async def get_agent(self, agent_id: str) -> Optional[Agent]:
        """Get an agent from the team by ID"""
        return await Agent.get(agent_id) if agent_id in self.assigned_agents else None

    async def list_agent_names(self) -> List[str]:
        """List all agent names in the team"""
        agents = await self.agents()
        return [agent.name for agent in agents]