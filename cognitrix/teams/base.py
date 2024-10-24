from typing import List, Optional, Dict, Any, Callable, TYPE_CHECKING
from pydantic import BaseModel, Field
import asyncio
from enum import Enum
from rich import print
from cognitrix.agents.base import Agent, Message, MessagePriority
from cognitrix.tasks.base import Task, TaskStatus
from odbms import Model
from functools import cached_property

if TYPE_CHECKING:
    from cognitrix.providers.base import LLM
    from cognitrix.providers.session import Session
    from cognitrix.utils.ws import WebSocketManager

class Team(Model):
    name: str
    """Name of the team"""
    
    description: str
    """Description of the team"""
    
    assigned_agents: List[str] = []
    """List of agent IDs in the team"""
    
    tasks: List[Task] = []
    """List of tasks in the team"""
    
    leader_id: Optional[str] = None
    """ID of the team leader"""

    class Config:
        arbitrary_types_allowed = True

    @cached_property
    def agents(self) -> List[Agent]:
        return [agent for aid in self.assigned_agents if (agent := Agent.get(aid)) is not None]

    @property
    def leader(self) -> Optional[Agent]:
        leader = Agent.get(self.leader_id) if self.leader_id else None
        if leader:
            from cognitrix.providers.base import LLM
            new_llm = LLM.load_llm(leader.llm.provider)
            if new_llm:
                new_llm.temperature = leader.llm.temperature
                leader.llm = new_llm
        return leader

    @leader.setter
    def leader(self, agent: Agent):
        if agent.id in self.assigned_agents:
            self.leader_id = agent.id
        else:
            raise ValueError("The leader must be a member of the team.")

    def add_agent(self, agent: Agent):
        if agent.id not in self.assigned_agents:
            self.assigned_agents.append(agent.id)

    def remove_agent(self, agent_id: str):
        self.assigned_agents = [aid for aid in self.assigned_agents if aid != agent_id]

    async def broadcast_message(self, sender: Agent, content: str, priority: MessagePriority = MessagePriority.NORMAL):
        for agent in self.agents:
            if agent.id != sender.id:
                message = Message(sender=sender.name, receiver=agent.name, content=content, priority=priority)
                await agent.receive_message(message)

    async def send_message(self, sender: Agent, receiver: Agent, content: str, priority: MessagePriority = MessagePriority.NORMAL, session: Optional['Session'] = None):
        message = Message(sender=sender.name, receiver=receiver.name, content=content, priority=priority)
        await receiver.receive_message(message, session)

    async def start_communication(self):
        while True:
            for agent in self.agents:
                while agent.response_list:
                    message, response = agent.response_list.pop(0)
                    processed_response = await self.process_agent_message(agent, message, response.result)
                    print(f"{agent.name} processed message from {message.sender}: {processed_response}")
            await asyncio.sleep(0.1)  # Small delay to prevent busy-waiting

    def create_task(self, title: str, description: str) -> Task:
        task = Task(title=title, description=description, assigned_agents=self.assigned_agents, team_id=self.id)
        task.save()
        self.tasks.append(task)
        return task

    def assign_task(self, task_id: str):
        check_assigned = next((t for t in self.tasks if t.id == task_id), None)
        if check_assigned:
            return check_assigned
        task = Task.get(task_id)
        if task:
            task.assigned_agents = self.assigned_agents
            task.status = TaskStatus.IN_PROGRESS
            task.team_id = self.id
            task.save()
            self.tasks.append(task)
            return task
        return None

    async def work_on_task(self, task_id: str, session: 'Session', websocket_manager: Optional['WebSocketManager'] = None):
        task = next((t for t in self.tasks if t.id == task_id), None)
        if task:
            try:
                # Planning phase
                task.status = TaskStatus.IN_PROGRESS
                task.save()
                print(f"Creating workflow for task: {task.title}")
                workflow = await self.leader_create_workflow(task, session, websocket_manager)
                print(workflow)
                # Execution and monitoring phase
                print(f"Executing workflow for task: {task.title}")
                result = await self.leader_coordinate_workflow(task, workflow, session, websocket_manager)
                
                # Evaluation and completion phase
                print(f"Evaluating and finalizing task: {task.title}")
                final_result = await self.leader_evaluate_and_finalize(task, result, session, websocket_manager)
                
                task.results = [final_result]
                task.status = TaskStatus.COMPLETED
                task.save()
            except ValueError as e:
                print(f"Error while working on task: {e}")
                task.status = TaskStatus.PENDING
                task.save()

    async def leader_create_workflow(self, task: Task, session: 'Session', websocket_manager: Optional['WebSocketManager'] = None) -> List[Dict[str, Any]]:
        response = ''
        workflow = []
        
        async def parse_agent_response(data: Dict[str, Any]):
            nonlocal response

            if not self.leader:
                raise ValueError("No team leader assigned to create the workflow.")
            
            response = response + data['content']

            if websocket_manager:
                await websocket_manager.send_team_message(self.leader.name, "system", data['content'])
        
        if not self.leader:
            raise ValueError("No team leader assigned to create the workflow.")
        
        prompt = (
            f"Task: {task.title}\nDescription: {task.description}\n"
            f"Team members: {', '.join(agent.name for agent in self.agents if agent.id != self.leader_id)}\n\n"
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
            "Do not forget to include 'Responsibilities:' right before the responsibilities."
        )

        await session(prompt, self.leader, 'task', True, parse_agent_response, {'type': 'start_task', 'action': 'create_workflow'})
        
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
            print(f"Warning: No workflow response received for task '{task.title}'")
            return []
        
        for step in workflow:
            for responsibility in step["responsibilities"]:
                agent = Agent.find_one({'name': responsibility["agent"]})
                if agent:
                    message = f"Your role in {step['step']} of task '{task.title}': {responsibility['task']}. Estimated time: {step['estimated_time']}"
                    await self.send_message(self.leader, agent, message, session=session)
        
        return workflow

    async def leader_coordinate_workflow(self, task: Task, workflow: List[Dict[str, Any]], session: 'Session', websocket_manager: Optional['WebSocketManager'] = None) -> str:
        from cognitrix.providers.base import LLM
        if not self.leader:
            raise ValueError("No team leader assigned to coordinate the workflow.")
        
        result: str = ''
        review_response: str = ''
        revision_response: str = ''
        
        async def parse_review_response(data: Dict[str, Any]):
            nonlocal review_response
            review_response = review_response + data['content']
        
        async def parse_revision_response(data: Dict[str, Any]):
            nonlocal revision_response
            revision_response = revision_response + data['content']
        
        for step in workflow:
            for responsibility in step["responsibilities"]:
                agent = Agent.find_one({'name': responsibility["agent"]})
                if agent:
                    # Leader assigns the task to the agent
                    message = f"Please start working on your part of the task: {task.title}\nStep: {step['step']}\nYour responsibility: {responsibility['task']}"
                    await self.send_message(self.leader, agent, message, session=session)
                    if websocket_manager:
                        await websocket_manager.send_team_message(self.leader.name, agent.name, message)
                    
                    # Agent works on the task
                    agent_result = await self.process_agent_task(agent, task, result, session, websocket_manager)
                    result += f"\n\n{agent_result}"
                    
                    # Leader reviews the agent's work
                    review_prompt = f"""Review the following work done by {agent.name} for the task '{task.title}' (Step: {step['step']}):

{agent_result}

Please provide a thorough review by addressing the following points:

1. Completeness: Is the response complete, or does it appear to be cut off mid-sentence?
2. Quality: Assess the overall quality of the work. Is it well-written, accurate, and relevant to the task?
3. Task Fulfillment: Does the response adequately address the assigned task and responsibilities?
4. Areas for Improvement: Identify any specific areas where the work could be enhanced or expanded upon.
5. Suggestions: Provide constructive feedback and specific suggestions for improvement if necessary.

After your review, conclude with one of the following actions:

- action: improve (if the work needs significant enhancements)
- action: revise (if minor revisions are needed)
- action: continue (if the response was cut off and needs to be continued)
- action: done (if the work is satisfactory and complete)

Your review:
"""
                    
                    await session(review_prompt, self.leader, 'task', True, parse_review_response, {'type': 'start_task', 'action': 'review_agent_task'})
                    
                    # Leader provides feedback to the agent
                    print(f"[!] Review response for {agent.name} on task '{task.title}' (Step: {step['step']}):\n{review_response}")
                    if review_response:
                        feedback, action = review_response.rsplit('action:', 1) # type: ignore
                        action = action.strip()
                        
                        # await self.send_message(self.leader, agent, f"Feedback on your work for task '{task.title}' (Step: {step['step']}):\n{feedback.strip()}", session=session)
                        print(f"[!] Action: {action}")
                        if action != 'done':
                            if action == 'improve':
                                revision_prompt = f"Based on the feedback, please significantly improve your work on the task: {task.title} (Step: {step['step']})"
                            elif action == 'revise':
                                revision_prompt = f"Based on the feedback, please make minor revisions to your work on the task: {task.title} (Step: {step['step']})"
                            elif action == 'continue':
                                revision_prompt = f"Your previous response was cut off. Please continue your work on the task: {task.title} (Step: {step['step']})"
                            else:
                                print(f"Warning: Unknown action '{action}' received for {agent.name}'s work on task '{task.title}' (Step: {step['step']})")
                                continue
                            
                            new_llm = LLM.load_llm(agent.llm.provider)
                            if new_llm:
                                new_llm.temperature = agent.llm.temperature
                                agent.llm = new_llm

                            await session(revision_prompt, agent, 'task', True, parse_revision_response, {'type': 'start_task', 'action': 'revision_agent_task'})
                            
                            if revision_response:
                                result += f"\n\nRevised work by {agent.name}: {revision_response}"
                    else:
                        print(f"Warning: No review response received for {agent.name}'s work on task '{task.title}' (Step: {step['step']})")
        
        return result

    async def leader_evaluate_and_finalize(self, task: Task, result: str, session: 'Session', websocket_manager: Optional['WebSocketManager'] = None) -> str:
        if not self.leader:
            raise ValueError("No team leader assigned to evaluate and finalize the task.")
        
        evaluation_response: str = ''
        
        async def parse_evaluation_response(data: Dict[str, Any]):
            nonlocal evaluation_response
            evaluation_response = evaluation_response + data['content']
            
        evaluation_prompt = f"Task: {task.title}\nFull results:\n{result}\nEvaluate the overall quality of the work, highlight key findings, and create a comprehensive summary."
        
        await session(evaluation_prompt, self.leader, 'task', True, parse_evaluation_response, {'type': 'start_task', 'action': 'evaluate_task'})
        
        if evaluation_response:
            final_result = f"Task Results:\n{result}\n\nTeam Leader Evaluation and Summary:\n{evaluation_response}"
            
            # Inform all team members about the task completion and share the summary
            for agent in self.agents:
                if agent.id != self.leader_id:
                    message = f"Task '{task.title}' has been completed. Here's the summary:\n{evaluation_response}"
                    await self.send_message(self.leader, agent, message, session=session)
                    if websocket_manager:
                        await websocket_manager.send_team_message(self.leader.name, agent.name, message)
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

    async def process_agent_task(self, agent: Agent, task: Task, previous_result: str, session: 'Session', websocket_manager: Optional['WebSocketManager'] = None) -> str:
        from cognitrix.providers.base import LLM
        
        prompt = (f"Task: {task.title}\nDescription: {task.description}\n"
                  f"Previous work done:\n{previous_result}\n"
                  f"Based on the previous work, please continue working on the task and provide your contribution.")
        
        new_llm = LLM.load_llm(agent.llm.provider)
        if new_llm:
            new_llm.temperature = agent.llm.temperature
            agent.llm = new_llm
        
        task_result: str = ''
        
        async def parse_agent_task_response(data: Dict[str, Any]):
            nonlocal task_result
            
            task_result = task_result + data['content']
            if websocket_manager:
                await websocket_manager.send_team_message(agent.name, "system", data["content"])
        
        await session(prompt, agent, 'task', True, parse_agent_task_response, {'type': 'start_task', 'action': 'process_agent_task'})
        
        return f"{agent.name}'s contribution: {task_result}"

    async def delegate_task(self, sender: Agent, message: Message, delegate_name: str):
        delegate = Agent.find_one({'name': delegate_name})
        if delegate:
            new_message = Message(
                sender=sender.name,
                receiver=delegate.name,
                content=f"Delegated task: {message.content}",
                priority=message.priority
            )
            await delegate.receive_message(new_message)
            return f"Task delegated to {delegate_name}"
        else:
            return f"Couldn't find agent {delegate_name} to delegate to"

    async def process_agent_message(self, agent: Agent, message: Message, response: str):
        if "delegate" in response.lower():
            delegate_name = response.split("delegate to:")[-1].strip()
            return await self.delegate_task(agent, message, delegate_name)
        else:
            return response

    def get_assigned_tasks(self):
        """Get all tasks assigned to this team"""
        return Task.find({'team_id': self.id})

    @classmethod
    def get_session(cls, team_id: str) -> 'Session':
        from cognitrix.providers.session import Session
        session = Session.find_one({'team_id': team_id})
        if not session:
            session = Session(team_id=team_id)
            session.save()
        return session

class TeamManager:
    def __init__(self):
        self.teams: Dict[str, Team] = {}

    def create_team(self, name: str, description: str) -> Team:
        team = Team(name=name, description=description)
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
    research_team = manager.create_team("Research Team", "A team dedicated to conducting cutting-edge research in artificial intelligence and machine learning.")

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
    research_team.assign_task(task.id)

    # Work on the task
    await research_team.work_on_task(task.id, research_team.get_session(research_team.id))

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
