from typing import Optional, Callable
from pydantic import BaseModel
import asyncio

class Task(BaseModel):
    """
    Initializes the Task object by assigning values to its attributes.

    Args:
        func (function): The function to be executed by the task.
        args (tuple): The positional arguments to be passed to the function.
        kwargs (dict): The keyword arguments to be passed to the function.

    Returns:
        None
    """
    
    description: str
    """Description of the task"""
    
    func: Optional[Callable] = None
    """Assigned tool to complete the task"""
    
    done: bool = False
    """Checks/Sets whether the task has been completed"""
    
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
