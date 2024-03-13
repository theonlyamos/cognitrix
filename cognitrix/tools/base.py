from pydantic import BaseModel, Field
from typing import Optional

class Tool(BaseModel):
    """
    Base tool class

    Args:
        BaseModel (_type_): _description_
    """
    
    name: str
    """Name of the tool"""
    
    description: str
    """Description of what the tool does and how to use it"""
    
    class Config:
        arbitrary_types_allowed = True
    
    def run(self, *args, **kwargs):
        """Here is where you code what the tool does"""
        pass
    
    async def arun(self, *args, **kwargs):
        """Asynchronous implementation"""
        pass