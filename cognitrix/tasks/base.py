import json
import asyncio
import logging
import uuid
import aiofiles
from datetime import datetime
from pydantic import BaseModel, Field
from typing import List, Literal, Optional, Callable, TypeAlias
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
    
    step_instructions: List = []
    """Line by line instructions for completing the task"""
    
    status: Literal['not-started', 'in-progress', 'completed'] = 'not-started'
    
    done: bool = False
    """Checks/Sets whether the task has been completed"""
    
    autostart: bool = False
    """Automatically start the task when it is ready"""
    
    created_at: str = (datetime.now()).strftime("%a %b %d %Y %H:%M:%S")
    """Creation date of the task"""
    
    completed_at: Optional[str] = None
    """Completion date of the task"""
    
    agent_ids: List[str] = []
    """List of ids of agents assigned to this task"""
    
    session_id: Optional[str] = None
    """Id of task session"""
    
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
                await self.save()
                print('[!]Starting task...\n')
                
                session.update_history({'role': 'system', 'type': 'text', 'message': self.description + '\n\nComplete the task step below:\n'})
                
                for index, step in enumerate(self.step_instructions):
                    if self.status == 'in-progress':
                        prompt = f'Step #{index + 1}: '+ step
                        await session(prompt, agent, streaming=True)
                        evaluator = Evaluator(llm=agent.llm)
                        eval_prompt = "Task: "+step
                        eval_prompt += "\n\nAgent Response:\n"+session.chat[-1]['message']
                        await session(eval_prompt, evaluator, streaming=True, save_history=False)
                
                self.status = 'completed'
                self.completed_at = (datetime.now()).strftime("%a %b %d %Y %H:%M:%S")
                await self.save()
    
    async def save(self):
        """Save current task"""
        self.step_instructions = Task.extract_steps(self.description)

        tasks = await Task.list_tasks()
        updated_tasks = []
        is_new = True
        for index, task in enumerate(tasks):
            if task.id == self.id:
                tasks[index] = self
                is_new = False
                break
        
        if is_new:
            tasks.append(self)
        
        for task in tasks:
            updated_tasks.append(task.dict())
        
        async with aiofiles.open(TASKS_FILE, 'w') as file:
            await file.write(json.dumps(updated_tasks, indent=4))
            
        return self.id

    @classmethod
    async def get(cls, id) -> Optional['Task']:
        try:
            tasks = await cls.list_tasks()
            loaded_tasks: List[Task] = [task for task in tasks if task.id == id]
            if len(loaded_tasks):
                return loaded_tasks[0]
        except Exception as e:
            logger.exception(e)
            return None
    
    @classmethod
    async def list_tasks(cls) -> TaskList:
        try:
            tasks: TaskList = []
            file_content: str = ''
            
            async with aiofiles.open(TASKS_FILE, 'r') as file:
                file_content = await file.read()
            
            loaded_tasks: list[dict] = json.loads(file_content) if file_content else []
            for task in loaded_tasks:
                loaded_task = cls(**task)
                tasks.append(loaded_task)

            return tasks

        except Exception as e:
            logger.exception(e)
            return []

    @staticmethod
    def extract_steps(text):
        # Find the start and end of the task_steps section
        if '<steps>' not in text:
            return []
        
        start = text.find('<steps>') + len('<steps>')
        end = text.find('</steps>')
        
        # Extract the content between the tags
        task_steps_content = text[start:end].strip()
        
        # Split the content into a list of steps
        steps_list = [step.strip() for step in task_steps_content.split('\n') if step.strip()]
        
        return steps_list
