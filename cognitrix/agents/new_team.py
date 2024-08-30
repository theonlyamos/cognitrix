from typing import List, Optional, Dict, Any, Callable
from pydantic import BaseModel, Field
import asyncio
import uuid
from enum import Enum
from cognitrix.agents.base import Agent as BaseAgent
from cognitrix.llms.base import LLMResponse
from queue import PriorityQueue

class MessagePriority(Enum):
    LOW = 1
    NORMAL = 2
    HIGH = 3
    URGENT = 4

class Message(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    sender: str
    receiver: str
    content: str
    priority: MessagePriority = MessagePriority.NORMAL
    read: bool = False

class TaskStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"

class Task(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    title: str
    description: str
    status: TaskStatus = TaskStatus.PENDING
    assigned_agents: List[str] = Field(default_factory=list)
    results: List[str] = Field(default_factory=list)

class Agent(BaseAgent):
    inbox: PriorityQueue = Field(default_factory=PriorityQueue)
    notification_callbacks: List[Callable[[Message], None]] = Field(default_factory=list)
    response_queue: asyncio.Queue = Field(default_factory=asyncio.Queue)
    team: Optional['Team'] = None

    def add_notification_callback(self, callback: Callable[[Message], None]):
        self.notification_callbacks.append(callback)

    def notify(self, message: Message):
        for callback in self.notification_callbacks:
            callback(message)

    async def receive_message(self, message: Message):
        self.inbox.put((-message.priority.value, message))
        self.notify(message)
        await self.process_messages()

    async def process_messages(self):
        while not self.inbox.empty():
            _, message = self.inbox.get()
            response = await self.process_message(message)
            await self.response_queue.put((message, response))
            message.read = True

    async def process_message(self, message: Message):
        content = f"{message.sender}: {message.content}"
        async for response in self.generate(content):
            print(f"{self.name} received: {message.content}")
            print(f"{self.name} responded: {response}")

        # Check for delegation keywords
            if "delegate" in str(response.text).lower():
                await self.delegate_task(message, str(response.text))
            elif "queue" in str(response.text).lower():
                await self.queue_response(message, str(response.text))
            else:
                return response

    async def delegate_task(self, message: Message, response: str):
        if self.team:
            # Extract delegate name from response (assuming format: "delegate to: AgentName")
            delegate_name = response.split("delegate to:")[-1].strip()
            delegate = self.team.get_agent_by_name(delegate_name)
            if delegate:
                new_message = Message(
                    sender=self.name,
                    receiver=delegate.name,
                    content=f"Delegated task: {message.content}",
                    priority=message.priority
                )
                await delegate.receive_message(new_message)
                return f"Task delegated to {delegate_name}"
            else:
                return f"Couldn't find agent {delegate_name} to delegate to"
        return "Unable to delegate task: not part of a team"

    async def queue_response(self, message: Message, response: str):
        # Extract time delay from response (assuming format: "queue for: 5 minutes")
        delay_str = response.split("queue for:")[-1].strip()
        try:
            delay_minutes = int(delay_str.split()[0])
            asyncio.create_task(self.send_delayed_response(message, delay_minutes))
            return f"Response queued for {delay_minutes} minutes"
        except ValueError:
            return "Invalid queue time format"

    async def send_delayed_response(self, message: Message, delay_minutes: int):
        await asyncio.sleep(delay_minutes * 60)
        async for response in  self.generate(f"Delayed response to: {message.content}"):
            await self.response_queue.put((message, response))

    class Config:
        arbitrary_types_allowed = True

class Team(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    name: str
    agents: List[Agent] = Field(default_factory=list)
    tasks: List[Task] = Field(default_factory=list)

    class Config:
        arbitrary_types_allowed = True

    def add_agent(self, agent: Agent):
        agent.team = self
        self.agents.append(agent)

    def remove_agent(self, agent_id: str):
        self.agents = [agent for agent in self.agents if agent.id != agent_id]
        for agent in self.agents:
            if agent.id == agent_id:
                agent.team = None

    async def broadcast_message(self, sender: Agent, content: str, priority: MessagePriority = MessagePriority.NORMAL):
        for agent in self.agents:
            if agent.id != sender.id:
                message = Message(sender=sender.name, receiver=agent.name, content=content, priority=priority)
                await agent.receive_message(message)

    async def send_message(self, sender: Agent, receiver: Agent, content: str, priority: MessagePriority = MessagePriority.NORMAL):
        message = Message(sender=sender.name, receiver=receiver.name, content=content, priority=priority)
        await receiver.receive_message(message)

    async def start_communication(self):
        while True:
            for agent in self.agents:
                while not agent.response_queue.empty():
                    message, response = await agent.response_queue.get()
                    print(f"{agent.name} processed message from {message.sender}: {response}")
            await asyncio.sleep(0.1)  # Small delay to prevent busy-waiting

    def get_agent_by_name(self, name: str) -> Optional[Agent]:
        return next((agent for agent in self.agents if agent.name.lower() == name.lower()), None)

    def create_task(self, title: str, description: str) -> Task:
        task = Task(title=title, description=description)
        self.tasks.append(task)
        return task

    def assign_task(self, task_id: str, agent_names: List[str]):
        task = next((t for t in self.tasks if t.id == task_id), None)
        if task:
            task.assigned_agents = agent_names
            task.status = TaskStatus.IN_PROGRESS

    async def work_on_task(self, task_id: str):
        task = next((t for t in self.tasks if t.id == task_id), None)
        if task and task.status == TaskStatus.IN_PROGRESS:
            for agent_name in task.assigned_agents:
                agent = self.get_agent_by_name(agent_name)
                if agent:
                    result = await self.process_agent_task(agent, task)
                    task.results.append(str(result))
            
            if len(task.results) == len(task.assigned_agents):
                task.status = TaskStatus.COMPLETED

    async def process_agent_task(self, agent: Agent, task: Task):
        prompt = f"Task: {task.title}\nDescription: {task.description}\nPlease work on this task and provide your contribution."
        async for response in agent.generate(prompt):
            return f"{agent.name}'s contribution: {response.llm_response}"

    def get_task_status(self, task_id: str) -> Optional[TaskStatus]:
        task = next((t for t in self.tasks if t.id == task_id), None)
        return task.status if task else None

    def get_task_results(self, task_id: str) -> Optional[List[str]]:
        task = next((t for t in self.tasks if t.id == task_id), None)
        return task.results if task else None

class TeamManager:
    def __init__(self):
        self.teams: Dict[str, Team] = {}

    def create_team(self, name: str) -> Team:
        team = Team(name=name)
        self.teams[team.id] = team
        return team

    def get_team(self, team_id: str) -> Optional[Team]:
        return self.teams.get(team_id)

    def delete_team(self, team_id: str):
        if team_id in self.teams:
            del self.teams[team_id]

    async def start_all_teams(self):
        tasks = [asyncio.create_task(team.start_communication()) for team in self.teams.values()]
        await asyncio.gather(*tasks)

    async def send_cross_team_message(self, sender_team: Team, sender_agent: Agent, 
                                      receiver_team: Team, receiver_agent: Agent, 
                                      content: str, priority: MessagePriority = MessagePriority.NORMAL):
        message = Message(
            sender=f"{sender_team.name}:{sender_agent.name}", 
            receiver=f"{receiver_team.name}:{receiver_agent.name}", 
            content=content,
            priority=priority
        )
        await receiver_agent.receive_message(message)

# Example usage
async def main():
    def print_notification(message: Message):
        print(f"Notification: New message for {message.receiver} from {message.sender} (Priority: {message.priority.name})")

    manager = TeamManager()
    research_team = manager.create_team("Research Team")

    alice = await Agent.create_agent(name="Alice", description="Research Assistant")
    bob = await Agent.create_agent(name="Bob", description="Data Analyst")
    charlie = await Agent.create_agent(name="Charlie", description="Domain Expert")

    alice.add_notification_callback(print_notification)
    bob.add_notification_callback(print_notification)
    charlie.add_notification_callback(print_notification)

    research_team.add_agent(alice)
    research_team.add_agent(bob)
    research_team.add_agent(charlie)

    # Start the communication system
    communication_task = asyncio.create_task(research_team.start_communication())

    # Send messages with different priorities
    await research_team.send_message(alice, bob, "Can you analyze the latest dataset?", MessagePriority.HIGH)
    await research_team.broadcast_message(charlie, "Team meeting at 2 PM today.", MessagePriority.NORMAL)
    await research_team.send_message(bob, alice, "Urgent: Client needs the report ASAP!", MessagePriority.URGENT)

    # Create and assign a task
    task = research_team.create_task(
        title="Analyze Customer Feedback",
        description="Review and summarize the latest customer feedback data to identify key trends and areas for improvement."
    )
    research_team.assign_task(task.id, ["Alice", "Bob"])

    # Work on the task
    await research_team.work_on_task(task.id)

    # Check task status and results
    task_status = research_team.get_task_status(task.id)
    if task_status == TaskStatus.COMPLETED:
        results = research_team.get_task_results(task.id)
        print(f"Task completed. Results: {results}")

    # Allow some time for message processing
    await asyncio.sleep(10)

    # Cancel the communication task
    communication_task.cancel()
    try:
        await communication_task
    except asyncio.CancelledError:
        pass

if __name__ == "__main__":
    asyncio.run(main())