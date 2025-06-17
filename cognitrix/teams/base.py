import asyncio
from functools import cached_property
from typing import TYPE_CHECKING, Any, Optional

from odbms import Model
from rich import print

from cognitrix.agents.base import Agent, Message, MessagePriority
from cognitrix.tasks.base import Task, TaskStatus

if TYPE_CHECKING:
    from cognitrix.sessions.base import Session
    from cognitrix.utils.ws import WebSocketManager

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

    async def work_on_task(self, task_id: str, session: 'Session', websocket_manager: Optional['WebSocketManager'] = None):
        return await TeamManager.work_on_task(self, task_id, session, websocket_manager)

    async def leader_create_workflow(self, task: Task, session: 'Session', websocket_manager: Optional['WebSocketManager'] = None) -> list[dict[str, Any]]:
        return await TeamManager.leader_create_workflow(self, task, session, websocket_manager)

    async def leader_coordinate_workflow(self, task: Task, workflow: list[dict[str, Any]], session: 'Session', websocket_manager: Optional['WebSocketManager'] = None) -> str:
        return await TeamManager.leader_coordinate_workflow(self, task, workflow, session, websocket_manager)

    async def leader_evaluate_and_finalize(self, task: Task, result: str, session: 'Session', websocket_manager: Optional['WebSocketManager'] = None) -> str:
        return await TeamManager.leader_evaluate_and_finalize(self, task, result, session, websocket_manager)

    def get_task_status(self, task_id: str) -> TaskStatus | None:
        task = next((t for t in self.tasks if t.id == task_id), None)
        return task.status if task else None

    def get_task_results(self, task_id: str) -> list[str] | None:
        task = next((t for t in self.tasks if t.id == task_id), None)
        return task.results if task else None

    async def process_agent_task(self, agent: Agent, task: Task, previous_result: str, session: 'Session', websocket_manager: Optional['WebSocketManager'] = None) -> str:
        return await TeamManager.process_agent_task(self, agent, task, previous_result, session, websocket_manager)

    async def delegate_task(self, sender: Agent, message: Message, delegate_name: str):
        return await TeamManager.delegate_task(self, sender, message, delegate_name)

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


class TeamManager:
    """
    Manager class for Team-related business logic.
    This class separates business logic from the data model,
    making the code more maintainable and testable.
    """

    def __init__(self):
        self.teams: dict[str, Team] = {}

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
            asyncio.create_task(team.start_communication())

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
    async def work_on_task(team: Team, task_id: str, session: 'Session', websocket_manager: Optional['WebSocketManager'] = None):
        """Coordinate team work on a task"""
        task = next((t for t in team.tasks if t.id == task_id), None)
        if task:
            try:
                # Planning phase
                task.status = TaskStatus.IN_PROGRESS
                await task.save()
                print(f"Creating workflow for task: {task.title}")
                workflow = await TeamManager.leader_create_workflow(team, task, session, websocket_manager)
                print(workflow)
                # Execution and monitoring phase
                print(f"Executing workflow for task: {task.title}")
                result = await TeamManager.leader_coordinate_workflow(team, task, workflow, session, websocket_manager)

                # Evaluation and completion phase
                print(f"Evaluating and finalizing task: {task.title}")
                final_result = await TeamManager.leader_evaluate_and_finalize(team, task, result, session, websocket_manager)

                task.results = [final_result]
                task.status = TaskStatus.COMPLETED
                await task.save()
            except ValueError as e:
                print(f"Error while working on task: {e}")
                task.status = TaskStatus.PENDING
                await task.save()

    @staticmethod
    async def leader_create_workflow(team: Team, task: Task, session: 'Session', websocket_manager: Optional['WebSocketManager'] = None) -> list[dict[str, Any]]:
        """Have the team leader create a workflow for the task"""
        response = ''
        workflow = []

        async def parse_agent_response(data: dict[str, Any]):
            nonlocal response

            if not await team.leader:
                raise ValueError("No team leader assigned to create the workflow.")

            response = response + data['content']

            if websocket_manager:
                leader = await team.leader
                if leader:
                    await websocket_manager.send_team_message(leader.name, "system", data['content'])

        if not await team.leader:
            raise ValueError("No team leader assigned to create the workflow.")

        leader = await team.leader # Fetch leader once
        agents_list = await team.agents # Fetch agents list once
        leader_id = leader.id if leader else None # Safely get leader ID
        member_names = [agent.name for agent in agents_list if agent.id != leader_id] # Create a list

        prompt = (
            f"Task: {task.title}\nDescription: {task.description}\n"
            f"Team members: {', '.join(member_names)}\n" # Join the list of names
            "All team members are AI agents. Factor this into your decision when giving time estimates.\n\n"
            "Create a detailed workflow for this task. For each step, provide the following information in a structured format:\n"
            "1. Step number and title\n"
            "2. Responsibilities (one per line, format: 'Agent Name: Specific task')\n"
            "3. Estimated time\n\n"
            "Use the following format for each step:\n"
            "Step X: [Step Title]\n"
            "Responsibilities:\n"
            "- [Agent Name]: [Specific task]\n"
            "- [Agent Name]: [Specific task]\n"
            "Estimated Time: [Duration]\n\n"
            "Repeat this structure for each step in the workflow."
            "Do not forget to include 'Responsibilities:' right before the responsibilities.\n"
            "Your response should not be in json format and should not contain any decorators."
        )

        await session(prompt, await team.leader, 'task', True, parse_agent_response, {'type': 'start_task', 'action': 'create_workflow'})

        if response:
            steps = response.split('\n\n')
            for step in steps: # type: ignore
                if step.strip().startswith("Step"):
                    lines = step.strip().split('\n')
                    current_step = {
                        "step": lines[0].strip(),
                        "responsibilities": [],
                        "estimated_time": ""
                    }

                    for line in lines[1:]:
                        if line.startswith("Responsibilities:"):
                            continue
                        elif line.startswith("- "):
                            agent, task_str = line[2:].split(": ")
                            current_step["responsibilities"].append({
                                "agent": agent.strip(),
                                "task": task_str.strip()
                            })
                        elif line.startswith("Estimated Time:"):
                            current_step["estimated_time"] = line.split(": ", 1)[1].strip()

                    workflow.append(current_step)

        if not workflow:
            raise ValueError("Failed to create workflow. Leader response was empty or malformed.")

        return workflow

    @staticmethod
    async def leader_coordinate_workflow(team: Team, task: Task, workflow: list[dict[str, Any]], session: 'Session', websocket_manager: Optional['WebSocketManager'] = None) -> str:
        """Have the team leader coordinate the workflow execution"""
        # Implementation would go here - simplified for now
        return "Workflow coordinated successfully"

    @staticmethod
    async def leader_evaluate_and_finalize(team: Team, task: Task, result: str, session: 'Session', websocket_manager: Optional['WebSocketManager'] = None) -> str:
        """Have the team leader evaluate and finalize the task results"""
        # Implementation would go here - simplified for now
        return f"Task '{task.title}' completed successfully"

    @staticmethod
    async def process_agent_task(team: Team, agent: Agent, task: Task, previous_result: str, session: 'Session', websocket_manager: Optional['WebSocketManager'] = None) -> str:
        """Process a task assigned to a specific agent"""
        # Implementation would go here - simplified for now
        return f"Agent {agent.name} completed their part of the task"

    @staticmethod
    async def delegate_task(team: Team, sender: Agent, message: Message, delegate_name: str):
        """Delegate a task to another team member"""
        # Implementation would go here - simplified for now
        pass

    @staticmethod
    async def process_agent_message(team: Team, agent: Agent, message: Message, response: str):
        """Process a message from an agent"""
        # Implementation would go here - simplified for now
        return f"Processed message from {agent.name}: {response[:50]}..."
