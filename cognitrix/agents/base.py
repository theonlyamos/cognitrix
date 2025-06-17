import asyncio
import logging
from collections.abc import Callable
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Optional, TypeAlias

from rich import print

from cognitrix.agents.templates import ASSISTANT_SYSTEM_PROMPT
from cognitrix.mcp.client import get_dynamic_client
from cognitrix.models import Agent, MCPTool, Message, Tool
from cognitrix.providers.base import LLM
from cognitrix.tools.base import ToolManager
from cognitrix.utils import extract_json
from cognitrix.utils.llm_response import LLMResponse

if TYPE_CHECKING:
    from cognitrix.sessions.base import Session

logger = logging.getLogger('cognitrix.log')

AgentList: TypeAlias = list['Agent']

class MessagePriority(Enum):
    LOW = 1
    NORMAL = 2
    HIGH = 3
    URGENT = 4

class AgentManager:
    """Handles the business logic for agents."""

    def __init__(self, agent: Agent):
        self.agent = agent

    def add_notification_callback(self, callback: Callable[[Message], None]):
        self.agent.notification_callbacks.append(callback)

    def notify(self, message: Message):
        for callback in self.agent.notification_callbacks:
            callback(message)

    async def receive_message(self, message: Message, session: Optional['Session'] = None): # type: ignore
        self.agent.inbox.append(message)
        self.notify(message)
        await self.process_messages(session)

    async def process_messages(self, session: Optional['Session'] = None): # type: ignore
        # Sort messages by priority (higher priority first)
        self.agent.inbox.sort(key=lambda m: m.priority.value, reverse=True)

        while self.agent.inbox:
            message = self.agent.inbox.pop(0)
            response = await self.process_message(message, session)
            self.agent.response_list.append((message, response))
            message.read = True

    async def process_message(self, message: Message, session: Optional['Session'] = None): # type: ignore
        content = f"{message.sender}: {message.content}"

        new_llm = LLM.load_llm(self.agent.llm.provider)
        if new_llm:
            new_llm.temperature = self.agent.llm.temperature
            self.agent.llm = new_llm

        result: str = ''

        async def generate_response(data: dict[str, Any]):
            nonlocal result
            result += data['content']

        if session:
            await session(message.content, self.agent, 'task', True, generate_response, {'type': 'start_task', 'action': 'process_message'})
        else:
            async for response in self.agent.generate(content):
                result += response.result # type: ignore

        print(f"\n{self.agent.name} responded: {result}")
        response = LLMResponse()
        response.add_chunk(result)

        return response

    @property
    def available_tools(self) -> list[str]:
        return [tool.name for tool in self.agent.tools]

    def formatted_system_prompt(self):
        tools_str = self._format_tools_string()
        subagents_str = self._format_subagents_string()
        llms_str = self._format_llms_string()

        today = (datetime.now()).strftime("%a %b %d %Y")
        prompt = f"Today is {today}.\n\n"
        prompt += self.agent.system_prompt
        prompt = prompt.replace("{name}", self.agent.name)

        if not self.agent.llm.supports_tool_use:
            prompt = prompt.replace("{tools}", tools_str)

        prompt = prompt.replace("{subagents}", subagents_str)
        prompt = prompt.replace("{llms}", llms_str)

        return prompt

    def _format_tools_string(self) -> str:
        return "You have access to the following Tools:\n" + "\n".join([f"{tool.name}: {tool.description}" for tool in self.agent.tools])

    def _format_subagents_string(self) -> str:
        if not len(self.agent.sub_agents):
            return ''
        subagents_str = "Available Subagents:\n"
        subagents_str += "\n".join([f"-- {agent.name}" for agent in self.agent.sub_agents])
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
            if self.agent.is_sub_agent:
                print("=======is sub agent===========")
                print(processed_query)

            if 'result' in processed_query.keys():
                result = processed_query['result']
                if isinstance(result, list):
                    if result[0] == 'image':
                        prompt['type'] = 'image'
                        prompt['content'] = result[1]
                    elif result[0] == 'agent':
                        new_agent: Agent = result[1]
                        new_agent.parent_id = self.agent.id
                        self.add_sub_agent(new_agent) # type: ignore

                        prompt['content'] = result[2]
                    else:
                        prompt['content'] = result[2]
                else:
                    prompt['content'] = result
            else:
                print(processed_query)
        else:
            prompt['content'] = processed_query

        return prompt

    def _process_query(self, query: str | dict) -> str | dict:
        return extract_json(query) if isinstance(query, str) else query

    def add_sub_agent(self, agent: Agent):
        self.agent.sub_agents.append(agent)

    def get_sub_agent_by_name(self, name: str) -> Agent | None:
        return next((agent for agent in self.agent.sub_agents if agent.name.lower() == name.lower()), None)

    def get_tool_by_name(self, name: str) -> Tool | None:
        return next((tool for tool in self.agent.tools if tool.name.lower() == name.lower()), None)

    async def call_tools(self, tool_calls: dict[str, Any] | list[dict[str, Any]]) -> dict[str, Any] | str:
        try:
            if tool_calls:
                agent_tool_calls = tool_calls if isinstance(tool_calls, list) else [tool_calls]
                tasks = []

                for t in agent_tool_calls:
                    tool = ToolManager.get_by_name(t['name'])

                    if not tool:
                        raise Exception(f"Tool '{t['name']}' not found")

                    print(f"\nRunning tool '{tool.name.title()}' with parameters: {t['arguments']}")
                    if 'sub agent' in tool.name.lower() or tool.name.lower() == 'create sub agent' or tool.category == 'mcp':
                        t['arguments']['parent'] = self.agent

                    tasks.append(asyncio.create_task(tool.run(**t['arguments'])))

                tool_calls_result = await asyncio.gather(*tasks)

                return {
                    'type': 'tool_calls_result',
                    'result': tool_calls_result
                }
        except Exception as e:
            print(e)
            return str(e)
        return ''

    def add_tool(self, tool: Tool):
        if tool not in self.agent.tools:
            self.agent.tools.append(tool)

    def add_mcp_server(self, server: str):
        if server not in self.agent.mcp_servers:
            self.agent.mcp_servers.append(server)

    def remove_mcp_server(self, server: str):
        if server in self.agent.mcp_servers:
            self.agent.mcp_servers.remove(server)

    async def generate(self, prompt: str):
        # This assumes the context manager will be used by the session/caller
        # to construct the full prompt before calling the LLM.
        processed_prompt = self.process_prompt(prompt)
        async for response in self.agent.llm([processed_prompt]):
            yield response

    def call_sub_agent(self, agent_name: str, task_description: str):
        pass

    async def init_mcp_tools(self):
        for server in self.agent.mcp_servers:
            await self.import_mcp_tools(server)

    async def import_mcp_tools(self, server: str):
        mcp_client = get_dynamic_client(server)
        if mcp_client:
            try:
                available_tools = await mcp_client.list_tools()
                for tool_def in available_tools:
                    def create_mcp_tool_runner(server_name, tool_name_to_call):
                        async def mcp_tool_runner(**kwargs):
                            client = get_dynamic_client(server_name)
                            if not client:
                                return f"MCP client for server '{server_name}' not found."
                            return await client.run_tool(tool_name_to_call, kwargs)
                        return mcp_tool_runner

                    mcp_tool = MCPTool(
                        name=tool_def['name'],
                        description=tool_def['description'],
                        category='mcp',
                        mcp_schema=tool_def.get('parameters', {}),
                        run=create_mcp_tool_runner(server, tool_def['name'])
                    )
                    self.add_tool(mcp_tool)
            except Exception as e:
                logger.error(f"Failed to import tools from MCP server '{server}': {e}")

    @staticmethod
    async def create_agent(name: str, system_prompt: str, provider: str = 'groq',
                           model: str | None = '', temperature: float = 0.0,  tools: list[str] = None,
                           mcp_servers: list[str] = None,
                           is_sub_agent: bool = False, parent_id=None,
                           ephemeral: bool = False) -> Agent | None:
        if mcp_servers is None:
            mcp_servers = []
        if tools is None:
            tools = []
        llm = LLM.load_llm(provider)
        if not llm:
            return None

        if model:
            llm.model = model
        llm.temperature = temperature

        loaded_tools: list[Tool] = []
        if tools:
            if 'all' in tools:
                loaded_tools = ToolManager.list_all_tools()
            else:
                for cat in tools:
                    cat_tools = ToolManager.get_tools_by_category(cat.strip().lower())
                    loaded_tools.extend(cat_tools)

                    tool_by_name = ToolManager.get_by_name(cat.strip().lower())
                    if tool_by_name:
                        loaded_tools.append(tool_by_name)

        agent = Agent(
            name=name,
            llm=llm,
            system_prompt=system_prompt or ASSISTANT_SYSTEM_PROMPT,
            tools=loaded_tools,
            mcp_servers=mcp_servers,
            is_sub_agent=is_sub_agent,
            parent_id=parent_id
        )

        if not ephemeral:
            await agent.save()
        return agent

    @staticmethod
    async def list_agents(parent_id: str | None = None) -> list[Agent]:
        if parent_id:
            return await Agent.find({'parent_id': parent_id})
        return await Agent.find({'is_sub_agent': False})

    @staticmethod
    async def load_agent(agent_name: str) -> Agent | None:
        return await Agent.find_one({'name': agent_name})
