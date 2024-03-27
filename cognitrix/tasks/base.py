from typing import Optional, Callable
from pydantic import BaseModel
import asyncio

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
    
    description: str
    """The task|query to perform|answer"""
    
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
