import inspect
import json
import sys
import uuid
import asyncio
import logging
import aiofiles
from pathlib import Path
from threading import Thread
from typing import List, Optional, Self, TypeAlias, Union, Type, Any
from dataclasses import dataclass

from pydantic import BaseModel, Field

from cognitrix.tasks import Task
from cognitrix.llms.base import LLM
from cognitrix.tools.base import Tool
from cognitrix.utils import extract_json, json_return_format
from cognitrix.agents.templates import AUTONOMOUSE_AGENT_2
from cognitrix.config import AGENTS_FILE, SESSIONS_FILE
from cognitrix.llms.session import Session
from cognitrix.transcriber import Transcriber

logger = logging.getLogger('cognitrix.log')

AgentList: TypeAlias = List['Agent']

class Agent(BaseModel):
    name: str = Field(default='Agent')
    llm: LLM
    tools: List[Tool] = Field(default_factory=list)
    prompt_template: str = Field(default=AUTONOMOUSE_AGENT_2)
    verbose: bool = Field(default=False)
    sub_agents: AgentList = Field(default_factory=list)
    task: Optional[Task] = None
    is_sub_agent: bool = Field(default=False)
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    parent_id: Optional[str] = None
    autostart: bool = False

    @property
    def available_tools(self) -> List[str]:
        return [tool.name for tool in self.tools]

    def format_system_prompt(self):
        tools_str = self._format_tools_string()
        subagents_str = self._format_subagents_string()
        llms_str = self._format_llms_string()

        prompt = self.prompt_template
        prompt = prompt.replace("{name}", self.name)
        prompt = prompt.replace("{tools}", tools_str)
        prompt = prompt.replace("{subagents}", subagents_str)
        prompt = prompt.replace("{available_tools}", json.dumps(self.available_tools))
        prompt = prompt.replace("{llms}", llms_str)
        prompt = prompt.replace("{return_format}", json_return_format)

        if 'json' not in prompt.lower():
            prompt += f"\n{json_return_format}"
        
        self.llm.system_prompt = prompt

    def _format_tools_string(self) -> str:
        return "You have access to the following Tools:\n" + "\n".join([f"{tool.name}: {tool.description}" for tool in self.tools])

    def _format_subagents_string(self) -> str:
        subagents_str = "Available Subagents:\n"
        subagents_str += "\n".join([f"-- {agent.name}: {agent.task.description}" for agent in self.sub_agents if agent.task])
        subagents_str += "\nYou should always use a subagent for a task if there is one specifically created for that task."
        subagents_str += "\nWhen creating a sub agent, it's description should be a comprehensive prompt of the agent's behavior, capabilities and example tasks."
        return subagents_str

    def _format_llms_string(self) -> str:
        llms = LLM.list_llms()
        llms_str = "Available LLM Providers:\n" + ", ".join(llms) + "\nChoose one for each subagent."
        return llms_str

    def generate_prompt(self, query: str | dict, role: str = 'User') -> dict:
        processed_query = self._process_query(query)
        prompt: dict[str, Any] = {'role': role, 'type': 'text'}

        if isinstance(processed_query, dict):
            if self.is_sub_agent:
                print("=======is sub agent===========")
                print(processed_query)
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

    async def process_response(self, response: str|dict) -> Union[dict, str]:
        # response = response.replace("'", '"')
        response_data = response
        if isinstance(response, str):
            response = response.replace('\\n', '')
            response = response.replace("'", "\"")
            # response = response.replace('"', '\\"')
            response_data = extract_json(response)
        

        try:
            if isinstance(response_data, dict):
                # final_result_keys = ['final_answer', 'function_call_result', 'respons']

                if response_data['type'].replace('\\', '') != 'function_call':
                    return response_data['result']

                tool = self.get_tool_by_name(response_data['function'])
                if isinstance(response_data['arguments'], dict):
                    response_data['arguments'] = list(response_data['arguments'].values())

                if response_data['function'].lower() == 'create agents':
                    response_data['arguments'] = [*response_data['arguments'], self.id]

                if not tool:
                    raise Exception(f"Tool {response_data['function']} not found")

                print(f"\nRunning tool '{tool.name.title()}' with parameters: {response_data['arguments']}")

                if 'sub agent' in tool.name.lower():
                    response_data['arguments'].append(self)
                    
                if tool.name.lower() == 'create sub agent':
                    result = await tool.arun(*response_data['arguments'])
                else:
                    result = tool.run(*response_data['arguments'])

                response_json = {
                    'type': 'function_call_result',
                    'result': result
                }

                return response_json
            else:
                raise Exception('Not a json object')
        except Exception as e:
            # logger.exception(e)
            return response_data

    def add_tool(self, tool: Tool):
        self.tools.append(tool)

    def initialize(self, session_id: Optional[str] = None):
        session: Session = asyncio.run(Session.load(session_id)) if session_id else Session(chat=self.llm.chat_history, agent_id=self.id)
        self.llm.chat_history = session.chat
        
        query: str | dict = input("\nUser (q to quit): ")
        while True:
            try:
                if not query:
                    query: str | dict = input("\nUser (q to quit): ")
                    continue
                if isinstance(query, str):
                    if query.lower() in ['q', 'quit', 'exit']:
                        print('Exiting...')
                        break

                    elif query.lower() == 'add agent':
                        new_agent = asyncio.run(self.create_agent(is_sub_agent=True, parent_id=self.id))
                        if new_agent:
                            self.add_sub_agent(new_agent)
                            print(f"\nAgent {new_agent.name} added successfully!")
                        else:
                            print("\nError creating agent")

                        query = input("\nUser (q to quit): ")
                        continue

                    elif query.lower() == 'list agents':
                        agents_str = "\nAvailable Agents:"
                        sub_agents = [agent for agent in asyncio.run(self.list_agents()) if agent.parent_id == self.id]
                        for index, agent in enumerate(sub_agents):
                            agents_str += (f"\n[{index}] {agent.name}")
                        print(agents_str)
                        query = input("\nUser (q to quit): ")
                        continue

                self.format_system_prompt()
                
                full_prompt = self.generate_prompt(query)
                response: Any = self.llm(full_prompt)
                
                self.llm.chat_history.append(full_prompt)
                self.llm.chat_history.append({'role': self.name, 'type': 'text', 'message': response})

                if self.verbose:
                    print(response)

                result: dict[Any, Any] | str = asyncio.run(self.process_response(response))

                if isinstance(result, dict) and result['type'] == 'function_call_result':
                    query = result
                else:
                    print(f"\n{self.name}: {result}")
                    query = input("\nUser (q to quit): ")
                
                self.save_session(session)

            except KeyboardInterrupt:
                print('Exiting...')
                break
            except Exception as e:
                logger.exception(e)
                break

    def start(self, session_id: Optional[str] = None, audio: bool = False):
        if audio:
            self.start_audio()
        else:
            self.initialize(session_id)
        
    def handle_transcription(self, sentence: str, transcriber: Transcriber):
        if sentence:
            self.format_system_prompt()
                
            full_prompt = self.generate_prompt(sentence)
            response: Any = self.llm(full_prompt)
            self.llm.chat_history.append(full_prompt)
            self.llm.chat_history.append({'role': self.name, 'type': 'text', 'message': response})

            if self.verbose:
                print(response)

            processsed_response: dict[Any, Any] | str = asyncio.run(self.process_response(response))

            if isinstance(processsed_response, dict) and processsed_response['type'] == 'function_call_result':
                query = processsed_response
            else:
                if isinstance(processsed_response, str):
                    transcriber.text_to_speech(processsed_response)
                print(f"\n{self.name}: {processsed_response}")


    def start_audio(self, **kwargs):
        transcriber = Transcriber(on_message_callback=self.handle_transcription)  # Pass callback to Transcriber
        if transcriber.start_transcription():
            print("Audio transcription started.")
            input("\n\nPress Enter to quit...\n\n")
            transcriber.stop_transcription()
        else:
            print("Failed to start audio transcription.")
        

    @staticmethod
    def start_task_thread(agent: 'Agent', parent: 'Agent'):
        agent_thread = Thread(target=agent.run_task, args=(parent,))
        agent_thread.name = agent.name.lower()
        agent_thread.start()

    def run_task(self, parent: Self):
        self.format_system_prompt()
        query = self.task.description if self.task else None

        while query:
            full_prompt = self.generate_prompt(query)
            response: Any = self.llm(full_prompt)
            self.llm.chat_history.append(full_prompt)
            self.llm.chat_history.append({'role': 'user', 'type': 'text', 'message': response})
            
            if parent.verbose:
                print(response)
                
            agent_result = asyncio.run(self.process_response(response))
            if isinstance(agent_result, dict) and agent_result['type'] == 'function_call_result':
                query = agent_result
            else:
                print(f"\n--{self.name}: {agent_result}")
                parent_prompt = parent.generate_prompt(response, 'user')
                parent_prompt['message'] = self.name + ": " + response
                parent_response: Any = parent.llm(parent_prompt)
                parent.llm.chat_history.append(parent_prompt)
                parent.llm.chat_history.append({'role': 'assistant', 'type': 'text', 'message': parent_response})
                parent_result = asyncio.run(parent.process_response(parent_response))
                print(f"\n\n{parent.name}: {parent_result}")
                query = ""

    def call_sub_agent(self, agent_name: str, task_description: str):
        sub_agent = self.get_sub_agent_by_name(agent_name)
        if sub_agent:
            sub_agent.task = Task(description=task_description)
            if sub_agent.task:
                self.start_task_thread(sub_agent, self)
        else:
            full_prompt = self.generate_prompt(f'Sub-agent with name {agent_name} was not found.')
            self.llm(full_prompt)

    @classmethod
    async def create_agent(cls, name: str = '', description: str = '', task_description: str = '', tools: List[Tool] = [],
                     llm: Optional[LLM] = None, is_sub_agent: bool = False, parent_id=None) -> Optional[Self]:
        try:
            name = name or input("\n[Enter agent name]: ")

            while not llm:
                llms = LLM.list_llms()
                llms_str = "\nAvailable LLMs:"
                for index, llm_l in enumerate(llms):
                    llms_str += (f"\n[{index}] {llm_l}")
                print(llms_str)
                agent_llm = int(input("\n[Select LLM]: "))
                selected_llm = llms[agent_llm]
                loaded_llm = LLM.load_llm(selected_llm)

                if loaded_llm:
                    llm = loaded_llm()
                    if llm:
                        llm.model = input(f"\nEnter model name [{llm.model}]: ") or llm.model
                        temp = input(f"\nEnter model temperature [{llm.temperature}]: ")
                        llm.temperature = float(temp) if temp else llm.temperature

            if llm:
                task = Task(description=task_description)
                new_agent = cls(name=name, llm=llm, task=task, tools=tools, is_sub_agent=is_sub_agent, parent_id=parent_id)
                
                if description:
                    new_agent.prompt_template = description

                agents = []
                async with aiofiles.open(AGENTS_FILE, 'r') as file:
                    content = await file.read()
                    agents = json.loads(content) if content else []
                    agents.append(new_agent.dict()) 

                async with aiofiles.open(AGENTS_FILE, 'w') as file:
                    await file.write(json.dumps(agents, indent=4))

                return new_agent

        except Exception as e:
            logger.error(str(e))

    @classmethod
    async def list_agents(cls, parent_id: Optional[str] = None) -> AgentList:
        try:
            agents: AgentList = []
            async with aiofiles.open(AGENTS_FILE, 'r') as file:
                content = await file.read()
                loaded_agents: list[dict] = json.loads(content) if content else []
                for agent in loaded_agents:
                    llm = LLM.load_llm(agent["llm"]["provider"])
                    loaded_agent = Agent(**agent)
                    if llm:
                        loaded_agent.llm = llm(**agent["llm"])
                    agents.append(loaded_agent)

            if parent_id:
                agents = [agent for agent in agents if agent.parent_id == parent_id]

            return agents

        except Exception as e:
            logger.exception(e)
            return []

    @classmethod
    async def get(cls, id) -> Optional['Agent']:
        try:
            agents = await cls.list_agents()
            loaded_agents: list[Agent] = [agent for agent in agents if agent.id == id]
            if len(loaded_agents):
                return loaded_agents[0]
        except Exception as e:
            logger.exception(e)
            return None

    @classmethod
    def load_agent(cls, agent_name: str) -> Optional['Agent']:
        try:
            agent_name = agent_name.lower()
            agents = asyncio.run(cls.list_agents())
            loaded_agents: list[Agent] = [agent for agent in agents if agent.name.lower() == agent_name]
            if len(loaded_agents):
                agent = loaded_agents[0]
                agent.sub_agents = asyncio.run(cls.list_agents(agent.id))
                return agent
        except Exception as e:
            logger.exception(e)
            return None
    
    async def save(self):
        """Save current agent"""
        agents = await Agent.list_agents()
        updated_agents = []
        for index, agent in enumerate(agents):
            if agent.id == self.id:
                agents[index] = self
            
            updated_agents.append(agent.dict())
        
        async with aiofiles.open(AGENTS_FILE, 'w') as file:
            await file.write(json.dumps(updated_agents, indent=4))
    
    def save_session(self, session: Session):
        save_thread = Thread(target=session.save, args=(self.llm.chat_history,))
        save_thread.daemon = True
        save_thread.start()