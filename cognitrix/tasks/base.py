import json
import asyncio
import logging
import uuid
import aiofiles
from datetime import datetime
from pydantic import BaseModel, Field
from typing import Any, Dict, List, Literal, Optional, Callable, Self, TypeAlias
from cognitrix.agents.evaluator import Evaluator

from cognitrix.config import TASKS_FILE
from cognitrix.agents.base import Agent
from cognitrix.llms.session import Session
from cognitrix.utils import xml_to_dict

logger = logging.getLogger('cognitrix.log')

TaskList: TypeAlias = List['Task']

class Task(BaseModel):
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
    
    status: Literal['not-started', 'in-progress', 'completed'] = 'not-started'
    
    done: bool = False
    """Checks/Sets whether the task has been completed"""
    
    autostart: bool = False
    """Automatically start the task when it is ready"""
    
    created_at: str = (datetime.now()).strftime("%a %b %d %Y %H:%M:%S")
    """Creation date of the task"""

    started_at: Optional[str] = None
    """Started date of the task"""
    
    completed_at: Optional[str] = None
    """Completion date of the task"""
    
    agent_ids: List[str] = []
    """List of ids of agents assigned to this task"""
    
    session_id: Optional[str] = None
    """Id of task session"""
    
    pid: Optional[str] = None
    """Worker Id of task"""
    
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    """Unique id for the task"""
    
    async def team(self):
        agents: List[Agent] = []
        for agent_id in self.agent_ids:
            agent = await Agent.get(agent_id)
            if agent:
                agents.append(agent)
        return agents

    async def session(self):
        if self.session_id:
            return await Session.load(self.session_id)
        else:
            new_session = Session()
            self.session_id = new_session.id
            await self.save()
            return new_session
    
    async def start(self):
        session = await self.session()

        if len(self.agent_ids):
            team = await self.team()

            if len(team):
                agent = team[0]
                
                if len(team) > 1:
                    agent.sub_agents = team[1:]
                
                self.status = 'in-progress'
                self.started_at = (datetime.now()).strftime("%a %b %d %Y %H:%M:%S")

                await self.save()
                
                session.update_history({'role': 'system', 'type': 'text', 'message': self.description + '\n\nComplete the task step below:\n'})
                
                print('[!]Starting task...\n', session)
                
                steps = self.step_instructions.copy()
                for key, value in steps.items():
                    if self.status == 'in-progress':
                        prompt = f'Step #{key + 1}: '+ value['step']
                        
                        await session(prompt, agent, streaming=True)
                        
                        evaluator = Evaluator(llm=agent.llm)
                        eval_prompt = "Task: "+value['step']
                        eval_prompt += "\n\nAgent Response:\n"+session.chat[-1]['message']
                        
                        await session(eval_prompt, evaluator, streaming=True, save_history=False)
                        self.step_instructions[key]['done'] = True
                        
                        await self.save()
                
                self.status = 'completed'
                self.completed_at = (datetime.now()).strftime("%a %b %d %Y %H:%M:%S")
                await self.save()
    
    @classmethod
    async def _load_tasks_from_file(cls) -> Dict[str, Dict]:
        async with aiofiles.open(TASKS_FILE, 'r') as file:
            content = await file.read()
            return json.loads(content) if content else {}

    @classmethod
    async def _save_tasks_to_file(cls, tasks: Dict[str, Dict]):
        async with aiofiles.open(TASKS_FILE, 'w') as file:
            await file.write(json.dumps(tasks, indent=4))

    async def save(self):
        """Save current task"""
        self.step_instructions = Task.extract_steps(self.description)
        tasks = await self._load_tasks_from_file()
        tasks[self.id] = self.dict()
        await self._save_tasks_to_file(tasks)
        return self.id

    @classmethod
    async def get(cls, id) -> Optional[Self]:
        tasks = await cls._load_tasks_from_file()
        task_data = tasks.get(id)
        if task_data:
            return cls(**task_data)
        return None

    @classmethod
    async def list_tasks(cls) -> TaskList:
        try:
            tasks = await cls._load_tasks_from_file()
            return [cls(**task_data) for task_data in tasks.values()]
        except Exception as e:
            logger.exception(e)
            return []

    @classmethod
    async def delete(cls, task_id: str):
        """Delete task by id"""
        tasks = await cls._load_tasks_from_file()
        if task_id in tasks:
            del tasks[task_id]
            await cls._save_tasks_to_file(tasks)
            return True
        return False

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