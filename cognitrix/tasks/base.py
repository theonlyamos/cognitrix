import logging
import uuid
from enum import Enum
from datetime import datetime
from pydantic import Field, validator
from typing import Any, Dict, List, Literal, Optional, Callable, Self, TypeAlias

from cognitrix.agents.evaluator import Evaluator
from cognitrix.agents.base import Agent
from cognitrix.sessions.base import Session

from odbms import Model

logger = logging.getLogger('cognitrix.log')

TaskList: TypeAlias = List['Task']

class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"

class Task(Model):
    """
    Initializes the Task object by assigning values to its attributes.

    Args:
        description (str): The task to perform or query to answer.
        args (tuple): The positional arguments to be passed to the function.
        kwargs (dict): The keyword arguments to be passed to the function.

    Returns:
        None
    """
    
    title: str
    """The title of the task"""
    
    description: str
    """The task|query to perform|answer"""
    
    step_instructions: Dict[int, Dict[str, Any]] = {}
    """Line by line instructions for completing the task"""
    
    done: bool = False
    """Checks/Sets whether the task has been completed"""
    
    autostart: bool = False
    """Automatically start the task when it is ready"""
    
    status: TaskStatus = Field(default=TaskStatus.PENDING)
    """Status of the task"""

    assigned_agents: List[str] = Field(default_factory=list)
    """List of ids of agents assigned to this task"""
    
    results: List[str] = Field(default_factory=list)
    """List of results from the task"""
    
    pid: Optional[str] = None
    """Worker Id of task"""
    
    team_id: Optional[str] = None
    """ID of the team assigned to this task"""

    async def team(self):
        agents: List[Agent] = []
        for agent_id in self.assigned_agents:
            agent = Agent.get(agent_id)
            if agent:
                agents.append(agent)
        return agents

    async def sessions(self):
        return await Session.get_by_task_id(self.id)
    
    async def start(self):
        if len(self.assigned_agents):
            team = await self.team()

            if len(team):
                agent = team[0]
                
                if len(team) > 1:
                    agent.sub_agents = team[1:]
                
                evaluator = Evaluator(llm=agent.llm)
                agent.sub_agents.append(evaluator)
                
                self.status = TaskStatus.IN_PROGRESS
                self.save()

                session = Session(task_id=self.id, agent_id=agent.id)
                session.started_at = (datetime.now()).strftime("%a %b %d %Y %H:%M:%S")
                session.save()
                
                session.update_history({'role': 'system', 'type': 'text', 'message': self.description + '\n\nComplete the task step below:\n'})
                
                print('[!]Starting task...\n')
                
                steps = self.step_instructions.copy()
                
                for key, value in steps.items():
                    if self.status == 'in-progress':
                        prompt = f'Step #{key + 1}: '+ value['step']
                        
                        await session(prompt, agent, stream=True)
                        
                        eval_prompt = "Task: "+value['step']
                        eval_prompt += "\n\nAgent Response:\n"+session.chat[-1]['message']
                        
                        await session(eval_prompt, evaluator, stream=True)
                        self.step_instructions[key]['done'] = True
                        
                        self.save()
                
                session.completed_at = (datetime.now()).strftime("%a %b %d %Y %H:%M:%S")
                session.save()
                
                self.status = TaskStatus.COMPLETED
                self.save()

    @classmethod
    async def list_tasks(cls) -> TaskList:
        return cls.all()

    @classmethod
    async def delete(cls, task_id: str):
        """Delete task by id"""
        return cls.remove({'id': task_id})

    @classmethod
    async def assign_to_team(cls, task_id: str, team_id: str):
        """Assign a task to a team"""
        task = cls.get(task_id)
        if task:
            task.team_id = team_id
            task.save()
            return task
        return None

    @staticmethod
    def extract_steps(text):
        # Find the start and end of the task_steps section
        if '<steps>' not in text:
            return {}
        
        steps: Dict[int, Dict[str, Any]] = {}
        start = text.find('<steps>') + len('<steps>')
        end = text.find('</steps>')
        
        # Extract the content between the tags
        task_steps_content = text[start:end].strip()
        
        # Split the content into a list of steps
        steps_list = [step.strip() for step in task_steps_content.split('\n') if step.strip()]
        
        for index, step in enumerate(steps_list):
            steps[index] = {'step': step, 'done': False}
        
        return steps

    @validator("status", pre=True)
    def parse_status(cls, value):
        if isinstance(value, TaskStatus):
            return value
        return TaskStatus(value)

    class Config:
        json_encoders = {
            TaskStatus: lambda v: v.value
        }