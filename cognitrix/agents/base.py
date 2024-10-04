import asyncio
import json
import logging
from rich import print
from enum import Enum
from datetime import datetime
from typing import Callable, Dict, List, Literal, Optional, Self, TypeAlias, Union, Type, Any

from fastapi import WebSocket
from pydantic import Field
from odbms import Model

from cognitrix.llms.base import LLM
from cognitrix.tools.base import Tool
from cognitrix.transcriber import Transcriber
from cognitrix.utils.llm_response import LLMResponse
from cognitrix.utils import extract_json, parse_tool_call_results
from cognitrix.agents.templates import ASSISTANT_SYSTEM_PROMPT

logger = logging.getLogger('cognitrix.log')

AgentList: TypeAlias = List['Agent']

class MessagePriority(Enum):
    LOW = 1
    NORMAL = 2
    HIGH = 3
    URGENT = 4

class Message(Model):
    sender: str
    receiver: str
    content: str
    priority: MessagePriority = MessagePriority.NORMAL
    read: bool = False

class Agent(Model):
    name: str = Field(default='Agent')
    """Name of the agent"""
    
    llm: LLM
    """LLM Provider to use for the agent"""
    
    tools: List[Tool] = Field(default=[])
    """List of tools to be use by the agent"""
    
    system_prompt: str = Field(default=ASSISTANT_SYSTEM_PROMPT)
    """Agent's prompt template"""
    
    verbose: bool = Field(default=False)
    """Set agent verbosity"""
    
    sub_agents: List[Self] = Field(default=[])
    """List of sub agents which can be called by this agent"""
    
    is_sub_agent: bool = Field(default=False)
    """Whether this agent is a sub agent for another agent"""
    
    parent_id: Optional[str] = None
    """Id of this agent's parent agent (if it's a sub agent)"""
    
    autostart: bool = False
    """Whether the agent should start running as soon as it's created"""
    
    websocket: Optional[WebSocket] = None
    """Websocket connection for web ui"""
    
    inbox: List[Message] = Field(default=[])
    """List of messages for the agent"""
    
    notification_callbacks: List[Callable[[Message], None]] = Field(default=[])
    """List of callbacks to be called when a message is added to the inbox"""
    
    response_list: List[tuple] = Field(default_factory=list)
    """List for storing responses from the agent"""
    
    class Config:
        arbitrary_types_allowed = True
    
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
            self.response_list.append((message, response))
            message.read = True

    async def process_message(self, message: Message):
        content = f"{message.sender}: {message.content}"
        
        new_llm = LLM.load_llm(self.llm.provider)
        if new_llm:
            new_llm.temperature = self.llm.temperature
            self.llm = new_llm
        
        response: LLMResponse
        async for response in self.generate(content):
            pass
        
        print(f"\n{self.name} responded: {response.result}")

        # Check for queue keyword
        if "queue" in str(response.result).lower():
            return await self.queue_response(message, str(response.result))
        else:
            return response

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
        async for response in self.generate(f"Delayed response to: {message.content}"):
            self.response_list.append((message, response))

    @property
    def available_tools(self) -> List[str]:
        return [tool.name for tool in self.tools]
    
    def formatted_system_prompt(self):
        tools_str = self._format_tools_string()
        subagents_str = self._format_subagents_string()
        llms_str = self._format_llms_string()

        today = (datetime.now()).strftime("%a %b %d %Y") 
        prompt = f"Today is {today}.\n\n"
        prompt += self.system_prompt
        prompt = prompt.replace("{name}", self.name)
        
        prompt = prompt.replace("{tools}", tools_str)
        prompt = prompt.replace("{subagents}", subagents_str)
        
        prompt = prompt.replace("{available_tools}", json.dumps(self.available_tools))
        prompt = prompt.replace("{llms}", llms_str)

        return prompt

    def _format_tools_string(self) -> str:
        return "You have access to the following Tools:\n" + "\n".join([f"{tool.name}: {tool.description}" for tool in self.tools])

    def _format_subagents_string(self) -> str:
        if not len(self.sub_agents):
            return ''
        subagents_str = "Available Subagents:\n"
        subagents_str += "\n".join([f"-- {agent.name}" for agent in self.sub_agents])
        subagents_str += "\nYou should always use a subagent for a task if there is one specifically created for that task."
        subagents_str += "\nWhen creating a sub agent, it's description should be a comprehensive prompt of the agent's behavior, capabilities and example tasks."
        return subagents_str

    def _format_llms_string(self) -> str:
        llms = LLM.list_llms()
        llms_str = "Available LLM Providers:\n" + ", ".join([llm.__name__ for llm in llms]) + "\nChoose one for each subagent."
        return llms_str

    def process_prompt(self, query: str | dict, role: str = 'User') -> dict:
        processed_query = self._process_query(query)
        prompt: dict[str, Any] = {'role': role, 'type': 'text'}

        if isinstance(processed_query, dict):
            if self.is_sub_agent:
                print("=======is sub agent===========")
                print(processed_query)

            if 'result' in processed_query.keys():
                result = processed_query['result']
                if isinstance(result, list):
                    if result[0] == 'image':
                        prompt['type'] = 'image'
                        prompt['image'] = result[1]
                    elif result[0] == 'agent':
                        new_agent: Agent = result[1]
                        new_agent.parent_id = self.id
                        self.add_sub_agent(new_agent) # type: ignore

                        prompt['message'] = result[2]
                    else:
                        prompt['message'] = result[2]
                else:
                    prompt['message'] = result
            else:
                print(processed_query)
        else:
            prompt['message'] = processed_query

        return prompt

    def _process_query(self, query: str | dict) -> str | dict:
        return extract_json(query) if isinstance(query, str) else query

    def add_sub_agent(self, agent: Self):
        self.sub_agents.append(agent)

    def get_sub_agent_by_name(self, name: str) -> Optional['Agent']:
        return next((agent for agent in self.sub_agents if agent.name.lower() == name.lower()), None)

    def get_tool_by_name(self, name: str) -> Optional[Tool]:
        return next((tool for tool in self.tools if tool.name.lower() == name.lower()), None)

    async def call_tools(self, tool_calls: dict) -> Union[dict, str]:
        print(f"Tool calls: {tool_calls}")
        try:
            if tool_calls:
                tool_calls_result = []
                agent_tool_calls = []

                if isinstance(tool_calls['tool'], list):
                    for t in tool_calls['tool']:
                        agent_tool_calls.append(t)
                else: 
                    agent_tool_calls.append(tool_calls['tool'])
                    
                for t in agent_tool_calls:
                    tool = Tool.get_by_name(t['name'])
                    
                    if not tool:
                        print(f"Tool '{t['name']}' not found")
                        raise Exception(f"Tool '{t['name']}' not found")
                    
                    print(f"\nRunning tool '{tool.name.title()}' with parameters: {t['arguments']}")
                    if 'sub agent' in tool.name.lower():
                        t['arguments']['parent'] = self
                    if tool.name.lower() == 'create sub agent':
                        t['arguments']['parent'] = self
                        result = await tool.arun(**t['arguments'])
                    else:
                        result = tool.run(**t['arguments'])
                    
                    tool_calls_result.append([tool.name, result])
                
                return {
                    'type': 'tool_calls_result',
                    'result': f"Tool calls result: {parse_tool_call_results(tool_calls_result)}"
                }
            else:
                raise Exception('Not a json object')
        except Exception as e:
            logger.exception(e)
            return str(e)

    def add_tool(self, tool: Tool):
        self.tools.append(tool)

    def generate(self, prompt: str):
        full_prompt = self.process_prompt(prompt)
        return self.llm(full_prompt, self.formatted_system_prompt())
    
    def call_sub_agent(self, agent_name: str, task_description: str):
        sub_agent = self.get_sub_agent_by_name(agent_name)

    @classmethod
    async def create_agent(cls, name: str, system_prompt: str, provider: str = 'groq', 
                           model: Optional[str] = '', temperature: float = 0.0,  tools: List[str] = [], 
                           is_sub_agent: bool = False, parent_id=None,
                           ephemeral: bool = False) -> Optional[Self]:
        try:
            name = name or input("\n[Enter agent name]: ")
            llm = LLM.load_llm(provider)

            if not llm:
                raise Exception('Error loading LLM')
            
            if model:
                llm.model = model
            if temperature:
                llm.temperature = temperature
            
            agent_tools = []
            if 'all' in tools:
                agent_tools = Tool.list_all_tools()
            else:
                loaded_tools = []
                for cat in tools:
                    loaded_tools = Tool.get_tools_by_category(cat.strip().lower())
                    
                    tool_by_name = Tool.get_by_name(cat.strip().lower())

                    if tool_by_name:
                        loaded_tools.append(tool_by_name)
                    agent_tools.extend(loaded_tools)
            
            new_agent = cls(name=name, llm=llm, system_prompt=system_prompt, tools=agent_tools, is_sub_agent=is_sub_agent, parent_id=parent_id) # type: ignore

            new_agent.save()

            return new_agent

        except Exception as e:
            logger.error(f"Error creating agent: {str(e)}")
            return None

    @classmethod
    async def list_agents(cls, parent_id: Optional[str] = None) -> List[Self]:
        return cls.all()

    @classmethod
    async def load_agent(cls, agent_name: str) -> Optional['Agent']:
        return cls.find_one({'name': agent_name})