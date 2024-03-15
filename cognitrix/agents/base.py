import re
import ast
import sys
import json
import asyncio
import logging
from threading import Thread
from typing import Optional, List, Dict, Union, Any, Self, Type

import uuid
from pydantic import BaseModel, Field

from ..llms.base import LLM
from ..tools.base import Tool
from ..tasks.base import Task
from ..agents.templates import ASSISTANT_TEMPLATE
from ..config import AGENTS_FILE

logging.basicConfig(
    format='%(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
    datefmt='%d-%b-%y %H:%M:%S',
    level=logging.WARNING
)
logger = logging.getLogger('cognitrix.log')

class Agent(BaseModel):
    """
    Base Agent Class
    """
    
    name: str = Field(default='Avatar')
    
    llm: LLM
    """Selected llm to use for agent"""
    
    tools: List[Tool] = Field(default=[])
    """Tools to be used by agent"""
    
    prompt_template: str = Field(default=ASSISTANT_TEMPLATE)
    """Base system prompt template"""
    
    verbose: bool = Field(default=False)
    """Verbose mode flag"""
    
    sub_agents: List['Agent'] = Field(default=[])
    """Sub agents that can be called by this agent"""
    
    task: Optional[Task] = None
    """Current task assigned to the agent"""
    
    is_sub_agent:  bool = Field(default=False)
    """Flag indicating if agent is a sub agent"""
    
    id: str = uuid.uuid4().hex
    """Unique ID for each agent instance"""
    
    parent_id: Optional[str] = None
    """ID of parent agent (if any)"""
    
    def __init__(self, **data):
        super().__init__(**data)
        
    def format_system_prompt(self):
        tools_str = "\n".join([f"{tool.name}: {tool.description}" for tool in self.tools])
        available_tools = [tool.name for tool in self.tools]
        prompt = self.prompt_template
        prompt = prompt.replace("{name}", self.name)
        # prompt = prompt.replace("{query}", query)
        prompt = prompt.replace("{tools}", tools_str)
        prompt = prompt.replace("{available_tools}", json.dumps(available_tools))
        
        self.llm.system_prompt = prompt
    
    def generate_prompt(self, query: str|dict, role: str = 'User')->dict:
        """Generates a prompt from a query.

        Args:
            query (str|dict): A string or dict containing the query.

        Returns:
           (dict): A dictionary containing the generated prompt.
        """
        
        processed_query = self.extract_json(query) if isinstance(query, str) else query
        
        if isinstance(processed_query, dict):
            if isinstance(processed_query['result'], list):
                if processed_query['result'][0] == 'image':
                    prompt = {'role': role, 'type': 'image', 'image': processed_query['result'][1]}
                elif processed_query['result'][0] == 'agents':
                    self.sub_agents = processed_query['result'][1]
                    for sub_agent in self.sub_agents:
                        agent_thread = Thread(target=sub_agent.start_task)
                        agent_thread.name = sub_agent.name.lower()
                        agent_thread.start()
                        
                    prompt = {'role': role, 'type': 'text', 'text': processed_query['result'][2]}
                else:
                    prompt = {'role': role, 'type': 'text', 'message': processed_query['result']}
            else:
                prompt = {'role': role, 'type': 'text', 'message': processed_query['result']}
        else:
            prompt = {'role': role, 'type': 'text', 'message': query}
 

        return {
            'type': 'query',
            'user_name': role,
            'output': prompt,
            'is_final': False
        }
    
    def add_sub_agent(self, agent: 'Agent'):
        """Adds a sub agent to the list of sub agents"""
        self.sub_agents.append(agent)
    
    def get_sub_agent_by_name(self, name: str)-> Optional['Agent']:
        for sub_agent in self.sub_agents:
            if sub_agent.name.lower() == name.lower():
                return sub_agent
        return None
    
    def get_tool_by_name(self, name: str)-> Optional[Tool]:
        for tool in self.tools:
            if tool.name.lower() == name.lower():
                return tool
        return None
    
    def process_response(self, response: str)-> Union[dict, str]:
        response = response.strip()
        response_data = self.extract_json(response)
        
        try:
            if isinstance(response_data, dict):
                final_result_keys = ['final_answer', 'function_call_result']
                
                if response_data['type'].replace('\\', '') in final_result_keys: # type: ignore
                    return response_data['result'] # type: ignore
                tool = self.get_tool_by_name(response_data['function']) # type: ignore
                if response_data['function'].lower() == 'create agents':
                    response_data['arguments'] = [*response_data['arguments'], self.id]
                
                if not tool:
                    raise Exception(f'Tool {response_data["function"]} not found') # type: ignore
               
                print(f"Running tool '{tool.name.title()}' with parameters: {response_data['arguments']}")
                
                if isinstance(response_data['arguments'], list): # type: ignore
                    result = tool.run(*response_data['arguments']) # type: ignore
                else:
                    result = tool.run(response_data['arguments']) # type: ignore

                # if isinstance(result, list) and result[0] == 'image':
                #     result = result[1]
                response_json = {}
                response_json['type'] = 'function_call_result'
                response_json['result'] = result
                
                return response_json
            else:
                raise Exception('Not a json object')
        except Exception as e:
            # logger.warning(str(e))
            return response_data
    
    def extract_json(self, content: str) -> dict | str:
        """
        Extract JSON content from a response string.

        Args:
            content (str): The response string to extract JSON from.

        Returns:
            dict|str: Result of the extraction.
        """
        try:
            # Escape special characters in the input string
            # escaped_content = re.escape(content)

            # Find the start and end index of the JSON string
            start_index = content.find('{')
            end_index = content.find('}', start_index) + 1

            # Extract the JSON string
            json_str = content[start_index:end_index]
            
            # Convert the JSON string to a Python dictionary
            json_dict = json.loads(json_str)
            return json_dict
        except Exception as e:
            # logger.warning(str(e))
            return content
    
    def add_tool(self, tool: Tool):
        """Adds an additional tool to this LLM object"""
        self.tools.append(tool)

    async def call_tool(self, tool, params):
        if asyncio.iscoroutinefunction(tool):
            return await tool(**params)
        else:
            return tool(**params)
    
    async def initialize(self):
        """
        Initialize the llm
        """
        self.format_system_prompt()
        
        query: str|dict = input("\nUser (q to quit): ")
        while query:
            try:
                if isinstance(query, str):
                    if query.lower() in ['q', 'quit', 'exit']:
                        print('Exiting...')
                        sys.exit(1)
                        
                    elif query.lower() == 'add agent':
                            new_agent = Agent.create_agent(is_sub_agent=True, parent_id=self.id)
                            if new_agent:
                                self.add_sub_agent(new_agent)
                                print(f"\nAgent {new_agent.name} added successfully!")
                            else:
                                print("\nError creating agent")
                            
                            query = input("\nUser (q to quit): ")
                            continue
                    
                    elif query.lower() == 'list agents':
                        agents_str = "\nAvailable Agents:"
                        agents = Agent.list_agents()
                        sub_agents = [agent for agent in agents if agent.parent_id == self.id]
                        for index, agent in enumerate(sub_agents):
                            agents_str += (f"\n[{index}] {agent.name}")
                        print(agents_str)
                        query = input("\nUser (q to quit): ")
                        continue
                
                full_prompt = self.generate_prompt(query)
                response = self.llm(full_prompt['output']) # type: ignore
                self.llm.chat_history.append(full_prompt['output'])
                self.llm.chat_history.append({'role': 'Assistant', 'type': 'text', 'message': response})

                if self.verbose:
                    print(response)
                
                result: dict[Any, Any] | str = self.process_response(response)
                
                if isinstance(result, dict) and result['type'] == 'function_call_result':
                    # self.llm.chat_history.append({'role': 'Assistant', 'type': 'text', 'message': response.strip()})
                    query = result
                else:
                    print(f"\n{self.name}: {result}")
                    query = input("\nUser (q to quit): ")
                    
            except KeyboardInterrupt:
                print('Exiting...')
                sys.exit(1)
            except Exception as e:
                logger.warning(str(e))
                sys.exit(1)

    def start(self):
        """
        Initialize agent
        """
        task = asyncio.run(self.initialize())

        return task
    
    def run_task(self, parent: type['Agent']):
        """Run agent task"""
        

        query = self.task.description if self.task else None
        
        while query and query.lower() != 'task complete!!!':
            full_prompt = self.generate_prompt(query)
            
            response = self.llm(full_prompt['output'])                                  #type: ignore
            self.llm.chat_history.append(full_prompt['output'])
            self.llm.chat_history.append({'role': self.name, 'type': 'text', 'message': response})
            
            parent_prompt = self.generate_prompt(response, self.name)
            parent.llm(parent_prompt)                                                     #type: ignore
            
            parent_response = self.llm(full_prompt['output']) # type: ignore
            parent.llm.chat_history.append(parent_response['output'])
            parent.llm.chat_history.append({'role': 'Assistant', 'type': 'text', 'message': response})
            
            print(f"\n{self.name}: {parent_response}")
            query = response
    
    def call_sub_agent(self, agent_name: str, task_description: str):
        """Run a task with a sub agent
        
        Args:
            agent_name (str): Name of the sub agent
            task_description (str): Description of the task for the sub agent
        
        Returns:
        """
        sub_agent = self.get_sub_agent_by_name(agent_name)
        if sub_agent:
            sub_agent.task = Task(description=task_description)
            
            query = sub_agent.task.description
            full_prompt = self.generate_prompt(query)
                
            response = self.llm(full_prompt['output'])                                  #type: ignore
            sub_agent.llm.chat_history.append(full_prompt['output'])
            sub_agent.llm.chat_history.append({'role': self.name, 'type': 'text', 'message': response})
            
            print(f"\n{sub_agent.name}: {response}")
            
            parent_prompt = self.generate_prompt(response, self.name)
            self.llm(parent_prompt['output'])                                                     #type: ignore
            
            parent_response = self.llm(full_prompt['output']) # type: ignore
            self.llm.chat_history.append(parent_response['output'])
            self.llm.chat_history.append({'role': 'Assistant', 'type': 'text', 'message': response})
            
            print(f"\n{self.name}: {parent_response}")
        else:
            full_prompt = self.generate_prompt(f'Sub-agent with name {agent_name} was not found.')
            self.llm(full_prompt['output'])         #type: ignore
    
    @classmethod
    def create_agent(cls,  name: str = '', task_description: str = '', tools: List[Tool]=[], llm: Optional[LLM]=None, is_sub_agent: bool = False, parent_id=None) -> Self | None:
        """Create a new agent instance

        Args:
            name (str): Name of the agent
            llm (LLM): LLM instance
            task_description (str): Task description
            tools (List[Tool], optional): List of tools available to the agent. Defaults to [].
            is_sub_agent (bool, optional): Set whether agent is a sub_agent. Defaults to True.
            parent_id (str, optional): Set parent agent id. Defaults to None.
        
        Returns:
            Agent: New agent instance
        """
        try:
            name = name or input("\n[Enter agent name]: ")
            task_description = task_description or input("\n[Enter brief description of agent task]: ")

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
                    llm.model = input(f"\nEnter model name [{llm.model}]: ") or llm.model
                    llm.temperature = float(input(f"\nEnter model temperature [{llm.temperature}]: ")) or llm.temperature
            if llm:   
                task = Task(description=task_description)
                new_agent = cls(name=name, llm=llm, task=task, tools=tools, is_sub_agent=is_sub_agent, parent_id=parent_id)
                with open(AGENTS_FILE, 'r') as file:
                    content = file.read()
                    agents = json.loads(content) if content else []
                    agents.append(new_agent.dict())        #type: ignore
                
                with open(AGENTS_FILE, 'w') as file:
                    json.dump(agents, file, indent=4)
                    
                return new_agent
            
        except Exception as e:
            logger.error(str(e))
            sys.exit(1)
    
    @staticmethod
    def list_agents(id: str = "")-> List['Agent']:
        """List all agents or just one agent when <id> is provided

        Args:
            id (str): Id of the agent (Optional)
        """
        try:
            agents: List['Agent'] = []
            with open(AGENTS_FILE, 'r') as file:
                content = file.read()
                loaded_agents: list[dict] = json.loads(content) if content else []
                # agents = [Agent(**agent) for agent in agents]   #type: ignore
                for agent in loaded_agents:
                    llm = LLM.load_llm(agent["llm"]["platform"])
                    loaded_agent = Agent(**agent)
                    if llm:
                        loaded_agent.llm = llm(**agent["llm"])
                    agents.append(loaded_agent)
            return agents
                
        except Exception as e:
            logger.exception(e)
            return []
        
    @classmethod
    def load_agent(cls, agent_name: str):
        """Dynamically load Agent based on name"""
        try:
            agent_name = agent_name.lower()
            agents = cls.list_agents()
            loaded_agents: list[Agent] = [agent for agent in agents if agent.name.lower() == agent_name]
            if len(loaded_agents):
                agent = loaded_agents[0]
                # loaded_llm = LLM.load_llm(agent.llm.platform)
                # if loaded_llm:
                #     agent.llm = loaded_llm(**agent.llm.dict())
                return agent
        except Exception as e:
            logger.exception(e)
            # logging.error(str(e))
            return None
        