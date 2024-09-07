from typing import List, Optional, Dict, Any, Callable
from pydantic import BaseModel, Field
import asyncio
import uuid
from enum import Enum
from cognitrix.agents.base import Agent as BaseAgent
from cognitrix.llms.base import LLMResponse

class MessagePriority(Enum):
    LOW = 1
    NORMAL = 2
    HIGH = 3
    URGENT = 4

class TaskStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"

class Message(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    sender: str
    receiver: str
    content: str
    priority: MessagePriority = MessagePriority.NORMAL
    read: bool = False

class Task(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    title: str
    description: str
    status: TaskStatus = TaskStatus.PENDING
    assigned_agents: List[str] = Field(default_factory=list)
    results: List[str] = Field(default_factory=list)

class Agent(BaseAgent):
    inbox: List[Message] = Field(default_factory=list)
    notification_callbacks: List[Callable[[Message], None]] = Field(default_factory=list)
    response_queue: asyncio.Queue = Field(default_factory=asyncio.Queue)
    team: Optional['Team'] = None

    def add_notification_callback(self, callback: Callable[[Message], None]):
        self.notification_callbacks.append(callback)

    def notify(self, message: Message):
        for callback in self.notification_callbacks:
            callback(message)

    async def receive_message(self, message: Message):
        self.inbox.append(message)
        self.notify(message)
        await self.process_messages()

    async def process_messages(self):
        # Sort messages by priority (higher priority first)
        self.inbox.sort(key=lambda m: m.priority.value, reverse=True)
        
        while self.inbox:
            message = self.inbox.pop(0)
            response = await self.process_message(message)
            await self.response_queue.put((message, response))
            message.read = True

    async def process_message(self, message: Message):
        content = f"{message.sender}: {message.content}"
        response: LLMResponse
        async for response in self.generate(content):
            pass
        
        # print(f"{self.name} received: {message.content}")
        print(f"{self.name} responded: {response.text}")

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
    _leader: Optional[Agent] = None

    class Config:
        arbitrary_types_allowed = True

    @property
    def leader(self) -> Optional[Agent]:
        return self._leader

    @leader.setter
    def leader(self, agent: Agent):
        if agent in self.agents:
            self._leader = agent
        else:
            raise ValueError("The leader must be a member of the team.")

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
                    print(f"{agent.name} processed message from {message.sender}: {response.llm_response}")
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
            try:
                # Planning phase
                workflow = await self.leader_create_workflow(task)
                
                # Execution and monitoring phase
                result = await self.leader_coordinate_workflow(task, workflow)
                
                # Evaluation and completion phase
                final_result = await self.leader_evaluate_and_finalize(task, result)
                
                task.results = [final_result]
                task.status = TaskStatus.COMPLETED
            except ValueError as e:
                print(f"Error while working on task: {e}")
                task.status = TaskStatus.PENDING

    async def leader_create_workflow(self, task: Task) -> List[str]:
        if not self.leader:
            raise ValueError("No team leader assigned to create the workflow.")
        
        prompt = (f"Task: {task.title}\nDescription: {task.description}\n"
                  f"Team members: {', '.join(agent.name for agent in self.agents if agent != self.leader)}\n"
                  "Create a detailed workflow assigning specific responsibilities to each team member, including the order of work and estimated time for each step.")
        response: LLMResponse
        async for response in self.leader.generate(prompt):
            pass
        
        workflow = []
        if response and response.llm_response:
            for line in response.llm_response.split('\n'):
                if ':' in line:
                    agent_name, responsibility = line.split(':', 1)
                    workflow.append(agent_name.strip())
                    agent = self.get_agent_by_name(agent_name.strip())
                    if agent:
                        await self.send_message(self.leader, agent, f"Your role in task '{task.title}': {responsibility.strip()}")
        else:
            print(f"Warning: No workflow response received for task '{task.title}'")
        
        return workflow

    async def leader_coordinate_workflow(self, task: Task, workflow: List[str]) -> str:
        if not self.leader:
            raise ValueError("No team leader assigned to coordinate the workflow.")
        
        result = ""
        for agent_name in workflow:
            agent = self.get_agent_by_name(agent_name)
            if agent:
                # Leader assigns the task to the agent
                await self.send_message(self.leader, agent, f"Please start working on your part of the task: {task.title}")
                
                # Agent works on the task
                agent_result = await self.process_agent_task(agent, task, result)
                result += f"\n\n{agent_result}"
                
                # Leader reviews the agent's work
                review_prompt = f"Review the following work done by {agent.name} for the task '{task.title}':\n{agent_result}\nProvide feedback and suggestions for improvement if necessary."
                review_response: LLMResponse
                async for review_response in self.leader.generate(review_prompt):
                    pass
                
                # Leader provides feedback to the agent
                if review_response and review_response.llm_response:
                    await self.send_message(self.leader, agent, f"Feedback on your work:\n{review_response.llm_response}")
                    
                    # If improvements are needed, the agent revises their work
                    if "improve" in review_response.llm_response.lower() or "revise" in review_response.llm_response.lower():
                        revision_prompt = f"Based on the feedback: {review_response.llm_response}\nPlease revise your work on the task: {task.title}"
                        revision_response: LLMResponse
                        async for revision_response in agent.generate(revision_prompt):
                            pass
                        if revision_response and revision_response.llm_response:
                            result += f"\n\nRevised work by {agent.name}: {revision_response.llm_response}"
                else:
                    print(f"Warning: No review response received for {agent.name}'s work on task '{task.title}'")
        
        return result

    async def leader_evaluate_and_finalize(self, task: Task, result: str) -> str:
        if not self.leader:
            raise ValueError("No team leader assigned to evaluate and finalize the task.")
        
        evaluation_prompt = f"Task: {task.title}\nFull results:\n{result}\nEvaluate the overall quality of the work, highlight key findings, and create a comprehensive summary."
        evaluation_response: LLMResponse
        async for evaluation_response in self.leader.generate(evaluation_prompt):
            pass
        
        if evaluation_response and evaluation_response.llm_response:
            final_result = f"Task Results:\n{result}\n\nTeam Leader Evaluation and Summary:\n{evaluation_response.llm_response}"
            
            # Inform all team members about the task completion and share the summary
            for agent in self.agents:
                if agent != self.leader:
                    await self.send_message(self.leader, agent, f"Task '{task.title}' has been completed. Here's the summary:\n{evaluation_response.llm_response}")
        else:
            final_result = f"Task Results:\n{result}\n\nWarning: No evaluation response received from the team leader."
            print(f"Warning: No evaluation response received for task '{task.title}'")
        
        return final_result

    def get_task_status(self, task_id: str) -> Optional[TaskStatus]:
        task = next((t for t in self.tasks if t.id == task_id), None)
        return task.status if task else None

    def get_task_results(self, task_id: str) -> Optional[List[str]]:
        task = next((t for t in self.tasks if t.id == task_id), None)
        return task.results if task else None

    async def process_agent_task(self, agent: Agent, task: Task, previous_result: str) -> str:
        prompt = (f"Task: {task.title}\nDescription: {task.description}\n"
                  f"Previous work done:\n{previous_result}\n"
                  f"Based on the previous work, please continue working on the task and provide your contribution.")
        response: LLMResponse
        async for response in agent.generate(prompt):
            pass
        return f"{agent.name}'s contribution: {response.llm_response}"

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

    alice = await Agent.create_agent(name="Alice", system_prompt="Research Assistant", ephemeral=True)
    bob = await Agent.create_agent(name="Bob", system_prompt="Data Analyst", ephemeral=True)
    charlie = await Agent.create_agent(name="Charlie", system_prompt="Domain Expert", ephemeral=True)

    if alice and bob and charlie:
        research_team.add_agent(alice)
        research_team.add_agent(bob)
        research_team.add_agent(charlie)
        research_team.leader = alice  # Set Alice as the team leader

    # Start the communication system
    # communication_task = asyncio.create_task(research_team.start_communication())

    # Send messages with different priorities
    # if alice and bob:
    #     await research_team.send_message(alice, bob, "Can you analyze the latest dataset?", MessagePriority.HIGH)
    # if charlie:
    #     await research_team.broadcast_message(charlie, "Team meeting at 2 PM today.", MessagePriority.NORMAL)
    # if bob and alice:
    #     await research_team.send_message(bob, alice, "Urgent: Client needs the report ASAP!", MessagePriority.URGENT)

    # Create and assign a task
    task = research_team.create_task(
        title="Stock Options",
        description="Get me best ai-related stock options to invest in"
    )
    research_team.assign_task(task.id, ["Alice", "Bob", "Charlie"])

    # Work on the task
    await research_team.work_on_task(task.id)

    # Check task status and results
    task_status = research_team.get_task_status(task.id)
    if task_status == TaskStatus.COMPLETED:
        results = research_team.get_task_results(task.id)
        if results is not None:
            print("Task completed. Results:")
            for result in results:
                print(result)
        else:
            print("Task completed, but no results were found.")
    else:
        print(f"Task is not completed. Current status: {task_status}")

    # Allow some time for message processing
    await asyncio.sleep(10)

    # Cancel the communication task
    # communication_task.cancel()
    # try:
    #     await communication_task
    # except asyncio.CancelledError:
    #     pass

if __name__ == "__main__":
    asyncio.run(main())