import json
import uuid
import logging
import aiofiles
from rich import print
from datetime import datetime
from typing import Dict, List, Literal, Optional, Self, TypeAlias, Union, Type, Any
from fastapi import WebSocket

from pydantic import BaseModel, Field

from cognitrix.llms.base import LLM, LLMResponse
from cognitrix.tools.base import Tool
from cognitrix.utils import extract_json, parse_tool_call_results
from cognitrix.agents.templates import ASSISTANT_SYSTEM_PROMPT
from cognitrix.config import AGENTS_FILE
# from cognitrix.llms.session import Session
from cognitrix.transcriber import Transcriber

logger = logging.getLogger('cognitrix.log')

AgentList: TypeAlias = List['Agent']

class Agent(BaseModel):
    name: str = Field(default='Agent')
    """Name of the agent"""
    
    llm: LLM
    """LLM Provider to use for the agent"""
    
    tools: List[Tool] = Field(default_factory=list)
    """List of tools to be use by the agent"""
    
    system_prompt: str = Field(default=ASSISTANT_SYSTEM_PROMPT)
    """Agent's prompt template"""
    
    verbose: bool = Field(default=False)
    """Set agent verbosity"""
    
    sub_agents: List[Self] = Field(default_factory=list)
    """List of sub agents which can be called by this agent"""
    
    is_sub_agent: bool = Field(default=False)
    """Whether this agent is a sub agent for another agent"""
    
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    """Unique id for the agent"""
    
    parent_id: Optional[str] = None
    """Id of this agent's parent agent (if it's a sub agent)"""
    
    autostart: bool = False
    """Whether the agent should start running as soon as it's created"""
    
    websocket: Optional[WebSocket] = None
    """Websocket connection for web ui"""
    
    class Config:
        arbitrary_types_allowed = True

    @property
    def available_tools(self) -> List[str]:
        return [tool.name for tool in self.tools]
    
    # def format_tools_for_llm(self):
    #     TYPE_HINTS = {
    #         "<class 'int'>": 'integer',
    #         "<class 'str'>": 'string',
    #         "<class 'float'>": 'float',
    #         "<class 'dict'>": 'object',
    #         "<class 'list'>": 'array',
    #         "typing.Dict": 'object',
    #         "typing.List": 'array',
    #         "typing.Optional[int]": 'integer',
    #         "typing.Optional[str]": 'integer',
    #         "typing.Optional[float]": 'float',
    #     }
    #     llm_tools: list[dict[str, Any]] = []
    #     classes = []
    #     for tool in self.tools:
    #         tool_parameters = tool.parameters
    #         if not tool_parameters:
    #             func_signatures = inspect.signature(tool.run)
    #             tool_parameters = func_signatures.parameters
            
    #         parameters = tool_parameters.keys()
    #         tool_details = {
    #             'tool': tool,
    #             'name': tool.name,
    #             'description': tool.description,
    #             'parameters': {},
    #             'required': []
    #         }

    #         if not 'args' in parameters:
    #             for name, param in tool_parameters.items():
    #                 print(name, param)
    #                 tool_details['parameters'] = {name: TYPE_HINTS.get(str(param), 'string')}
    #             tool_details['required'] = [name for name, param in tool_parameters.items() if param.default is inspect._empty]
            
    #         llm_tools.append(tool_details)

    #     self.llm.format_tools(llm_tools)

    def formatted_system_prompt(self):
        tools_str = self._format_tools_string()
        subagents_str = self._format_subagents_string()
        llms_str = self._format_llms_string()

        today = (datetime.now()).strftime("%a %b %d %Y") 
        prompt = f"Today is {today}.\n\n"
        prompt += self.system_prompt
        prompt = prompt.replace("{name}", self.name)
        
        # if not self.llm.supports_tool_use:
        prompt = prompt.replace("{tools}", tools_str)
        prompt = prompt.replace("{subagents}", subagents_str)
        
        # if not self.llm.supports_tool_use:
        prompt = prompt.replace("{available_tools}", json.dumps(self.available_tools))
        prompt = prompt.replace("{llms}", llms_str)
        # prompt = prompt.replace("{return_format}", json_return_format)

        # self.llm.system_prompt = prompt
        
        # self.format_tools_for_llm()
        
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
                        self.add_sub_agent(new_agent)

                        prompt['message'] = result[2]
                    else:
                        prompt['message'] = result
                else:
                    prompt['message'] = result
            else:
                print(processed_query)
        else:
            prompt['message'] = processed_query

        return prompt

    def _process_query(self, query: str | dict) -> str | dict:
        return extract_json(query) if isinstance(query, str) else query

    def add_sub_agent(self, agent: 'Agent'):
        self.sub_agents.append(agent)

    def get_sub_agent_by_name(self, name: str) -> Optional['Agent']:
        return next((agent for agent in self.sub_agents if agent.name.lower() == name.lower()), None)

    def get_tool_by_name(self, name: str) -> Optional[Tool]:
        return next((tool for tool in self.tools if tool.name.lower() == name.lower()), None)

    async def call_tools(self, tool_calls: dict) -> Union[dict, str]:
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
                    # if self.websocket:
                    #     await self.websocket.send_text(json.dumps({
                    #         'type': 'chat_message', 
                    #         'content': f"\nRunning tool '{tool.name.title()}' with parameters: {t['arguments']}\n", 
                    #         'action': 'reply', 'complete': False}))
                        
                    if 'sub agent' in tool.name.lower():
                        t['arguments']['parent'] = self
                    # print(tool)
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

    # async def chat(self, user_input: str | dict, session: Session):
    #     self.format_system_prompt()
        
    #     full_response = ''
    #     streaming = True
    #     tool_calls = False
        
    #     while streaming:
    #         full_prompt = self.process_prompt(user_input)
    #         response: LLMResponse | None = None
    #         async for response in self.llm(full_prompt):
    #             full_response = response.text
                
    #             if response.text:
    #                 response_text = response.text
    #                 if response.before and response.before != '```xml':
    #                     response_text = response.before + '\n\n' + response.text
    #                 if response.after and response.after != '```':
    #                     response_text = response_text + '\n\n' + response.after
                    
    #                 if response.artifacts:
    #                     print(response.artifacts)
    #                 yield json.dumps({'type': 'chat_message', 'content': response_text, 'action': 'reply', 'complete': True})
    #             else:
    #                 yield json.dumps({'type': 'chat_message', 'content': response.current_chunk, 'action': 'reply', 'complete': False})
    #             await asyncio.sleep(0.5)
    #             if response.tool_calls:
    #                 result: dict[Any, Any] | str = await self.call_tools(response.tool_calls)
    #                 if isinstance(result, dict) and result['type'] == 'tool_calls_result':
    #                     user_input = result
    #                     tool_calls = True
    #                     response.tool_calls = None
    #                 else:
    #                     yield json.dumps({'type': 'chat_message', 'content': result, 'action': 'reply'})
        
    #         if full_response:
    #             self.llm.chat_history.append(full_prompt)
    #             self.llm.chat_history.append({'role': self.name, 'type': 'text', 'message': full_response})
            
    #         if not tool_calls:
    #             streaming = False
        
    #     self.save_session(session)
    
    # async def initialize(self, session_id: Optional[str] = None, interface: Literal['cli', 'api', 'websocket'] = 'cli', stream: bool = False):
    #     session: Session = await self.load_session(session_id)
        
    #     query: str | dict = input("\nUser (q to quit): ")
    #     while True:
    #         try:
    #             if not query:
    #                 query: str | dict = input("\nUser (q to quit): ")
    #                 continue
    #             if isinstance(query, str):
    #                 if query.lower() in ['q', 'quit', 'exit']:
    #                     print('Exiting...')
    #                     break

    #                 elif query.lower() == 'add agent':
    #                     new_agent = asyncio.run(self.create_agent(is_sub_agent=True, parent_id=self.id))
    #                     if new_agent:
    #                         self.add_sub_agent(new_agent)
    #                         print(f"\nAgent {new_agent.name} added successfully!")
    #                     else:
    #                         print("\nError creating agent")

    #                     query = input("\nUser (q to quit): ")
    #                     continue

    #                 elif query.lower() == 'list tools':
    #                     agents_str = "\nAvailable Tools:"
    #                     tools = [tool for tool in self.tools]
    #                     for index, tool in enumerate(tools):
    #                         agents_str += (f"\n[{index}] {tool.name}")
    #                     print(agents_str)
    #                     query = input("\nUser (q to quit): ")
    #                     continue
                    
    #                 elif query.lower() == 'list agents':
    #                     tools_str = "\nAvailable Agents:"
    #                     sub_agents = [agent for agent in asyncio.run(self.list_agents()) if agent.parent_id == self.id]
    #                     for index, agent in enumerate(sub_agents):
    #                         tools_str += (f"\n[{index}] {agent.name}")
    #                     print(tools_str)
    #                     query = input("\nUser (q to quit): ")
    #                     continue
                    
    #                 elif query.lower() == 'show history':
    #                     history_str = "\nChat History:"
    #                     session = await self.load_session()
    #                     history = session.chat
    #                     for index, chat in enumerate(history):
    #                         history_str += (f"\n[{chat['role']}]: {chat['message']}\n")
    #                     print(history_str)
    #                     query = input("\nUser (q to quit): ")
    #                     continue

    #             self.format_system_prompt()
                
    #             full_prompt = self.process_prompt(query)

    #             query = ''
    #             response: LLMResponse | None = None
    #             called_tools: bool = False
    #             async for response in self.llm(full_prompt):
    #                 if self.verbose:
    #                     print(f"{response.current_chunk}", end="")
                    
    #                 if response.tool_calls and not called_tools and not response.text:
    #                     called_tools = True
    #                     result: dict[Any, Any] | str = await self.call_tools(response.tool_calls)
                        
    #                     if isinstance(result, dict) and result['type'] == 'tool_calls_result':
    #                         query = result
    #                     else:
    #                         print(result)
    #                 # else:
    #                 #     query = input("\nUser (q to quit): ")
                
    #             self.llm.chat_history.append(full_prompt)
    #             if response:
    #                 self.llm.chat_history.append({'role': self.name, 'type': 'text', 'message': ''.join(response.chunks)})
    #                 if response.text:
    #                     print(f"\n{self.name}:", response.text)
                
    #             self.save_session(session)
                
    #             if not query:
    #                 query = input("\nUser (q to quit): ")
                

    #         except KeyboardInterrupt:
    #             print('Exiting...')
    #             break
    #         except Exception as e:
    #             logger.exception(e)
    #             break

    def generate(self, prompt: str):
        full_prompt = self.process_prompt(prompt)
        return self.llm(full_prompt, self.formatted_system_prompt())
    
    # async def start(self, session_id: Optional[str] = None, audio: bool = False):
    #     if audio:
    #         self.start_audio()
    #     else:
    #         await self.initialize(session_id)
        
    # def handle_transcription(self, sentence: str, transcriber: Transcriber):
    #     if sentence:
    #         self.format_system_prompt()
                
    #         full_prompt = self.process_prompt(sentence)
    #         response: Any = self.llm(full_prompt)
    #         self.llm.chat_history.append(full_prompt)
    #         self.llm.chat_history.append({'role': self.name, 'type': 'text', 'message': response})

    #         if self.verbose:
    #             print(response)

    #         processsed_response: dict[Any, Any] | str = asyncio.run(self.call_tools(response))

    #         if isinstance(processsed_response, dict) and processsed_response['type'] == 'tool_calls_result':
    #             query = processsed_response
    #         else:
    #             if isinstance(processsed_response, str):
    #                 transcriber.text_to_speech(processsed_response)
    #             print(f"\n{self.name}: {processsed_response}")


    # def start_audio(self, **kwargs):
    #     transcriber = Transcriber(on_message_callback=self.handle_transcription)  # Pass callback to Transcriber
    #     if transcriber.start_transcription():
    #         print("Audio transcription started.")
    #         input("\n\nPress Enter to quit...\n\n")
    #         transcriber.stop_transcription()
    #     else:
    #         print("Failed to start audio transcription.")
        

    # @staticmethod
    # def start_task_thread(agent: 'Agent', parent: 'Agent'):
    #     agent_thread = Thread(target=agent.run_task, args=(parent,))
    #     agent_thread.name = agent.name.lower()
    #     agent_thread.start()

    # def run_task(self, parent: Self):
    #     self.format_system_prompt()
    #     query = self.task.description if self.task else None

    #     while query:
    #         full_prompt = self.process_prompt(query)
    #         response: Any = self.llm(full_prompt)
    #         self.llm.chat_history.append(full_prompt)
    #         self.llm.chat_history.append({'role': 'user', 'type': 'text', 'message': response})
            
    #         if parent.verbose:
    #             print(response)
                
    #         agent_result = asyncio.run(self.call_tools(response))
    #         if isinstance(agent_result, dict) and agent_result['type'] == 'tool_calls_result':
    #             query = agent_result
    #         else:
    #             print(f"\n--{self.name}: {agent_result}")
    #             parent_prompt = parent.process_prompt(response, 'user')
    #             parent_prompt['message'] = self.name + ": " + response
    #             parent_response: Any = parent.llm(parent_prompt)
    #             parent.llm.chat_history.append(parent_prompt)
    #             parent.llm.chat_history.append({'role': 'assistant', 'type': 'text', 'message': parent_response})
    #             parent_result = asyncio.run(parent.call_tools(parent_response))
    #             print(f"\n\n{parent.name}: {parent_result}")
    #             query = ""

    def call_sub_agent(self, agent_name: str, task_description: str):
        sub_agent = self.get_sub_agent_by_name(agent_name)
        # if sub_agent:
            # sub_agent.task = Task(description=task_description)
            # if sub_agent.task:
            #     self.start_task_thread(sub_agent, self)
        # else:
        #     full_prompt = self.process_prompt(f'Sub-agent with name {agent_name} was not found.')
        #     self.llm(full_prompt, self.formatted_system_prompt())

    @classmethod
    async def _load_agents_from_file(cls) -> Dict[str, Dict]:
        async with aiofiles.open(AGENTS_FILE, 'r') as file:
            content = await file.read()
            return json.loads(content) if content else {}

    @classmethod
    async def _save_agents_to_file(cls, agents: Dict[str, Dict]):
        async with aiofiles.open(AGENTS_FILE, 'w') as file:
            await file.write(json.dumps(agents, indent=4))

    @classmethod
    async def create_agent(cls, name: str = '', description: str = '', tools: List[Tool] = [],
                     llm: Optional[LLM] = None, is_sub_agent: bool = False, parent_id=None) -> Optional[Self]:
        try:
            name = name or input("\n[Enter agent name]: ")

            while not llm:
                llms = LLM.list_llms()
                llms_str = "\nAvailable LLMs:"
                for index, llm_l in enumerate(llms):
                    llms_str += (f"\n[{index}] {llm_l.__name__}")
                print(llms_str)
                
                agent_llm = int(input("\n[Select LLM]: "))
                loaded_llm = llms[agent_llm]
                
                if loaded_llm:
                    llm = loaded_llm()
                    if llm:
                        llm.model = input(f"\nEnter model name [{llm.model}]: ") or llm.model
                        temp = input(f"\nEnter model temperature [{llm.temperature}]: ")
                        llm.temperature = float(temp) if temp else llm.temperature

            if not llm:
                raise Exception('Error loading LLM')
                
            description = description or input("\n[Enter agent system prompt]: ")
            
            new_agent = cls(name=name, llm=llm, tools=tools, is_sub_agent=is_sub_agent, parent_id=parent_id)
            
            if description:
                new_agent.system_prompt = description

            agents = await cls._load_agents_from_file()
            agents[new_agent.id] = new_agent.dict()

            await cls._save_agents_to_file(agents)

            return new_agent

        except Exception as e:
            logger.error(f"Error creating agent: {str(e)}")
            return None

    @classmethod
    async def list_agents(cls, parent_id: Optional[str] = None) -> List[Self]:
        try:
            loaded_agents = await cls._load_agents_from_file()
            agents = []
            
            for agent_data in loaded_agents.values():
                agent = Agent(**agent_data)

                # llm = LLM.load_llm(agent.llm.provider)
                # print(llm)
                # if llm:
                #     agent.llm = llm(**agent.llm.dict())
                agents.append(agent)

            if parent_id:
                agents = [agent for agent in agents if agent and agent.parent_id == parent_id]

            return agents

        except Exception as e:
            logger.exception(e)
            return []

    @classmethod
    async def get(cls, id) -> Optional[Self]:
        agents = await cls._load_agents_from_file()
        agent_data = agents.get(id)
        if agent_data:
            agent =  Agent(**agent_data)
            provider = LLM.load_llm(agent_data['llm']['provider'])
            if provider:
                agent.llm = provider(**agent_data['llm'])
            return agent
        return None

    @classmethod
    async def load_agent(cls, agent_name: str) -> Optional['Agent']:
        agents = await cls._load_agents_from_file()
        agent_data = next((data for data in agents.values() if data['name'].lower() == agent_name.lower()), None)
        if agent_data:
            agent = Agent(**agent_data)
            agent.sub_agents = await cls.list_agents(agent.id)
            return agent
        return None
    
    async def save(self):
        """Save current agent"""
        agents = await self._load_agents_from_file()
        agents[self.id] = self.dict()
        await self._save_agents_to_file(agents)
        return self.id
        
    @classmethod    
    async def delete(cls, name_or_id: str):
        """Delete agent by id or name"""
        agents = await cls._load_agents_from_file()
        
        if name_or_id in agents:
            del agents[name_or_id]
        else:
            for agent_id, agent_data in list(agents.items()):
                if agent_data['name'].lower() == name_or_id.lower():
                    del agents[agent_id]
                    break
        
        if len(agents) < len(await cls._load_agents_from_file()):
            await cls._save_agents_to_file(agents)
            return True
        return False