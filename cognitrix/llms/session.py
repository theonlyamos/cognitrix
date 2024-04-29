import asyncio
import logging
import aiofiles
from flask import json
from pydantic import BaseModel, Field
from cognitrix.config import SESSIONS_FILE
from typing import List, Optional, Dict
from datetime import datetime
import uuid

class Session(BaseModel):
    chat: List[Dict[str, str]] = []
    """The chat history of the session"""
    
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    """The session id"""
    
    datetime: str = (datetime.now()).strftime("%a %b %d %Y %H:%M:%S")
    """When the session was started"""
    
    agent_id: str = ""
    """The id of the agent that started the session"""
    
    def save(self, chat: List[Dict[str, str]] = []):
        """Save the current state of the session to disk"""
        self.chat = chat
        sessions = Session.list_sessions()
        updated_sessions = []
        session_exist = False
        
        for index, session in enumerate(sessions):
            if session.id == self.id:
                sessions[index] = self
                session_exist = True
            
            updated_sessions.append(session.model_dump())
        
        if not session_exist:
            updated_sessions.append(self.model_dump())
            
        with open(SESSIONS_FILE, 'w') as file:
            json.dump(updated_sessions, file, indent=4)

    @staticmethod
    def list_sessions() -> List['Session']:
        """List all available sessions from disk"""
        sessions = []
        try:
            with open(SESSIONS_FILE, 'r') as file:
                content = file.read()
                sessions = json.loads(content)if content else []
                return [Session(**session) for session in sessions]
        except Exception as e:
            logging.exception(e)
            return []

    @classmethod
    async def load(cls, session_id: str):
        try:
            sessions = cls.list_sessions()
            loaded_sessions: list[Session] = [session for session in sessions if session.id == session_id]
            if len(loaded_sessions):
                session = loaded_sessions[0]
                return session
            else:
                return Session()
        except Exception as e:
            logging.exception(e)
            return Session()