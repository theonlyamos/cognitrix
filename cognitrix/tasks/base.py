import json
import asyncio
import logging
import uuid
import aiofiles
from datetime import datetime
from pydantic import BaseModel, Field
from typing import List, Literal, Optional, Callable, TypeAlias

from cognitrix.config import TASKS_FILE

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
    
    func: Optional[Callable] = None
    """Assigned tool to complete the task"""
    
    status: Literal['not-started', 'in-progress', 'completed'] = 'not-started'
    
    done: bool = False
    """Checks/Sets whether the task has been completed"""
    
    autostart: bool = False
    """Automatically start the task when it is ready"""
    
    created_at: str = (datetime.now()).strftime("%a %b %d %Y %H:%M:%S")
    """Creation date of the task"""
    
    completed_at: Optional[str] = None
    """Completion date of the task"""
    
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    """Unique id for the task"""
    
    async def start(self):
        if self.func:
            self.task = asyncio.create_task(self.func())

    async def join(self):
        if self.task:
            await self.task
        else:
            print("Task not started yet")
    
    async def cancel(self):
        if self.task:
            self.task.cancel()
        else:
            print("Task not started yet")
    
    async def save(self):
        """Save current task"""

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
