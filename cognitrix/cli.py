import os
import sys
import asyncio
import logging
import argparse
from pathlib import Path
from argparse import Namespace
from fastapi import Request
from rich import print
from functools import lru_cache

from fastapi.responses import JSONResponse

from cognitrix.providers import LLM
from cognitrix.agents import  Agent
from cognitrix.sessions.base import Session
from cognitrix.tasks.base import Task
from cognitrix.tools.base import Tool
from cognitrix.teams.base import Team
from cognitrix.utils.ws import WebSocketManager
from cognitrix.utils.sse import SSEManager
from cognitrix.agents.templates import ASSISTANT_SYSTEM_PROMPT

from cognitrix.config import VERSION, run_configure

import subprocess

logger = logging.getLogger('cognitrix.log')
parser = argparse.ArgumentParser(description="Build and run AI agents on your computer")

async def start_web_ui(agent: Agent):
    from .api.main import app
    from fastapi import WebSocket
    import uvicorn
    ws_manager = WebSocketManager(agent)
    # sse_manager = SSEManager(agent)

    @app.middleware("http")
    async def add_middleware_data(request: Request, call_next):
        request.state.agent = agent
        # request.state.sse_manager = sse_manager
        response = await call_next(request)
        return response

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        await ws_manager.websocket_endpoint(websocket) # type: ignore

    # Use uvicorn's Server class for async operation instead of uvicorn.run()
    config = uvicorn.Config(app, host="0.0.0.0", port=8000, forwarded_allow_ips="*")
    server = uvicorn.Server(config)
    await server.serve()
    
def add_agent():
    name = None
    provider = None
    system_prompt = None
    
    try:
        while not name:
            name = input("Enter agent name: ")
            while not provider:
                llms = LLM.list_llms()
                llms_str = "\nAvailable LLMs:"
                for index, llm_l in enumerate(llms):
                    llms_str += (f"\n[{index}] {llm_l.__name__}")
                print(llms_str)
                
                agent_llm = int(input("\n[Select LLM]: "))
                loaded_llm = llms[agent_llm]
                
                if loaded_llm:
                    provider = loaded_llm()
                    if provider:
                        provider.model = input(f"\nEnter model name [{provider.model}]: ") or provider.model
                        temp = input(f"\nEnter model temperature [{provider.temperature}]: ")
                        provider.temperature = float(temp) if temp else provider.temperature

            while not system_prompt:
                system_prompt = input("\n[Enter agent system prompt]: ")
                
            new_agent = asyncio.run(Agent.create_agent(name, system_prompt=system_prompt, provider=provider, is_sub_agent=True))
            
            if not new_agent:
                raise Exception("Error creating agent")
            
            print(f"\nAgent **{new_agent.name}** added successfully!")
    except Exception as e:
        logger.error(str(e))
    finally:
        sys.exit()
    
def delete_agent(agent_name_or_index: str):
    if agent_name_or_index:
        agent_deleted1 = Agent.remove({'id': agent_name_or_index})
        agent_deleted2 = Agent.remove({'name': agent_name_or_index})
        if agent_deleted1 or agent_deleted2:
            print(f"\nAgent **{agent_name_or_index}** deleted successfully!")
        else:
            print(f"Agent **{agent_name_or_index}** couldn't be deleted")
    else:
        print("\nError deleting agent")
    sys.exit()

@lru_cache(maxsize=None)
def get_agents():
    return Agent.all()

@lru_cache(maxsize=None)
def get_tasks():
    return Task.all()

@lru_cache(maxsize=None)
def get_teams():
    return Team.all()

def list_teams():
    teams = get_teams()
    team_names = [team.name for team in teams]
    max_col = len(max(team_names, key=len))
    max_width = 10
    max_col = max_col if max_col >= max_width else max_width
    
    print("\nAvailable Teams:")
    print(f" {'_'*(int(len(teams)/10)+3)} {'_'*(max_col+2)}")
    print(f"| #{' '*(int(len(teams)/10))} | {'Task Title'+' '*(max_col-max_width)} |")
    print(f"|{'-'*(int(len(teams)/10)+3)}|-{'-'*(max_col)}-|")
    for index, t in enumerate(teams):
        padding = (len(str(index)))-int(len(teams)/10)
        padding = padding if padding else padding + 2
        print(f"| {str(index+1) + ' '*padding}| {t.name + ' '*(max_col-len(t.name))} |")
    print(f"|{'-'*(int(len(teams)/10)+3)}|{'-'*(max_col+2)}|")
    print()
    
def list_tasks():
    tasks = get_tasks()
    task_names = [task.title for task in tasks]
    max_col = len(max(task_names, key=len))
    max_width = 10
    max_col = max_col if max_col >= max_width else max_width
    
    print("\nAvailable Tasks:")
    print(f" {'_'*(int(len(tasks)/10)+3)} {'_'*(max_col+2)}")
    print(f"| #{' '*(int(len(tasks)/10))} | {'Task Title'+' '*(max_col-max_width)} |")
    print(f"|{'-'*(int(len(tasks)/10)+3)}|-{'-'*(max_col)}-|")
    for index, t in enumerate(tasks):
        padding = (len(str(index)))-int(len(tasks)/10)
        padding = padding if padding else padding + 2
        print(f"| {str(index+1) + ' '*padding}| {t.title + ' '*(max_col-len(t.title))} |")
    print(f"|{'-'*(int(len(tasks)/10)+3)}|{'-'*(max_col+2)}|")
    print()

def list_agents():
    agents = get_agents()
    agent_names = [agent.name for agent in agents]
    max_col = len(max(agent_names, key=len))
    max_width = 10
    max_col = max_col if max_col >= max_width else max_width
    
    print("\nAvailable Agents:")
    print(f" {'_'*(int(len(agents)/10)+3)} {'_'*(max_col+2)}")
    print(f"| #{' '*(int(len(agents)/10))} | {'Agent Name'+' '*(max_col-max_width)} |")
    print(f"|{'-'*(int(len(agents)/10)+3)}|{'-'*(max_col+1)}-|")
    for index, a in enumerate(agents):
        padding = (len(str(index)))-int(len(agents)/10)
        padding = padding if padding else padding + 2
        print(f"| {str(index+1) + ' '*padding}| {a.name + ' '*(max_col-len(a.name))} |")
    print(f"|{'-'*(int(len(agents)/10)+3)}|{'-'*(max_col+2)}|")
    print()
    
@lru_cache(maxsize=None)
def get_providers():
    return LLM.list_llms()

def list_providers():
    providers = get_providers()
    provider_names = [p.__name__ for p in providers]
    max_col = len(max(provider_names, key=len))
    max_width = 9
    max_col = max_col if max_col >= max_width else max_width

    print("\nAvailable Providers:")
    print(f" {'_'*(int(len(providers)/10)+3)} {'_'*(max_col+2)}")
    print(f"| #{' '*(int(len(providers)/10))} | {'Provider'+' '*(max_col-7)}|")
    print(f"|{'-'*(int(len(providers)/10)+3)}|{'-'*(max_col+1)}-|")
    for index, p in enumerate(providers):
        padding = (len(str(index)))-int(len(providers)/10)
        padding = padding if padding else padding + 2
        print(f"| {str(index+1) + ' '*padding}| {p.__name__ + ' '*(max_col-len(p.__name__))} |")
    print(f"|{'-'*(int(len(providers)/10)+3)}|{'-'*(max_col+2)}|")
    print()

@lru_cache(maxsize=None)
def get_tools(category='all'):
    return Tool.get_tools_by_category(category)

def list_tools(category='all'):
    tools = get_tools(category)
    tool_names = [t.name for t in tools]
    max_col = len(max(tool_names, key=len))
    max_width = 13
    max_col = max_col if max_col >= max_width else max_width

    print("\nAvailable Tools:")
    print(f" {'_'*((int(len(tools)/10)+5)+max_col+max_width+4)}")
    print(f"| #{' '*(int(len(tools)/10))} | {'Tool Name'+' '*(max_col-9)} | {'Tool category'+' '*(max_width-13)} |")
    print(f"|{'-'*(int(len(tools)/10)+3)}|-{'-'*(max_col)}-|-{'-'*(max_width)}-|")
    for index, t in enumerate(tools):
        padding = (len(str(index)))-int(len(tools)/10)
        padding = padding if padding else padding + 2
        print(f"| {str(index) + ' '*padding}| {t.name + ' '*(max_col-len(t.name))} | {t.category + ' '*(max_width-len(t.category))} |")
        
async def list_sessions():
    print("\nSaved Sessions:")
    sessions = await Session.list_sessions()
    for index, s in enumerate(sessions):
        print(f"[{index}] [{s.datetime}] {s.id}")

def manage_agents(args: Namespace):
    try:
        if args.new:
            add_agent()
        elif args.delete:
            agent_id__or_name = args.id or args.name
            if not agent_id__or_name:
                raise Exception('Specify agent name or id to delete')
            delete_agent(agent_id__or_name)
        elif args.list:
            list_agents()
    except KeyboardInterrupt:
        print()
        sys.exit()
    except Exception as e:
        logger.exception(e)
        sys.exit(1)
        
def manage_tools(args: Namespace):
    try:
        if args.list:
            list_tools(args.list)
    except KeyboardInterrupt:
        print()
        sys.exit()
    except Exception as e:
        logging.exception(e)
        sys.exit(1)

def str_or_file(string):
    if len(string) > 100:
        return string
    if Path(string).is_file() or Path(os.curdir, string).is_file():
        with open(Path(string), 'rt') as file:
            return file.read()
    return string

async def prompt_agent(assistant: Agent, prompt):
    async for response in assistant.generate(prompt):
        if response.result:
            print(f"\r{response.result}", end='')
    print()

async def initialize(session: Session, agent: Agent, stream: bool = False):
    while True:
        try:
            query: str | dict = input("\nUser (q to quit): ").lower()
            if not query:
                continue

            if query in ['q', 'quit', 'exit']:
                print('Exiting...')
                break

            command_handlers = {
                'add agent': add_agent,
                'list tools': lambda: print("\nAvailable Tools:\n" + "\n".join(f"[{i}] {tool.name}" for i, tool in enumerate(agent.tools))),
                'list agents': lambda: print("\nAvailable Agents:\n" + "\n".join(f"[{i}] {a.name}" for i, a in enumerate(get_agents()) if a.parent_id == agent.id)),
                'show history': lambda: print("\nChat History:\n" + "\n".join(f"[{chat['role']}]: {chat['message']}" for chat in session.chat))
            }

            if query in command_handlers:
                command_handlers[query]()
            else:
                await session(query, agent, stream=stream)

        except KeyboardInterrupt:
            print('Exiting...')
            break
        except Exception as e:
            logger.exception(e)
            break


async def start(args: Namespace):
    run_configure()
    # try:
    if args.providers:
        list_providers()
        sys.exit()
    elif args.agents:
        list_agents()              
        sys.exit()
    elif args.tasks:
        list_tasks()
        sys.exit()
    elif args.teams:
        list_teams()
        sys.exit()
    elif args.sessions:
        await list_sessions()
        sys.exit()
    
    assistant = None
    
    if args.agent:
        assistant = await Agent.find_one({'name': args.agent})
    if not assistant:
        assistant = await Agent.create_agent(name=args.agent, provider=args.provider, model=args.model, temperature=args.temperature, system_prompt=ASSISTANT_SYSTEM_PROMPT)
    
        
    if not assistant:
        raise Exception("Agent not found")

    if args.provider:
        provider = LLM.load_llm(args.provider)
        if provider:
            provider.provider = args.provider
            assistant.llm = provider
    
    if args.model and (assistant.llm.model.lower() != args.model.lower()):
        assistant.llm.model = args.model
    
    # if args.temperature and (assistant.llm.temperature != args.temperature):
    #     assistant.llm.temperature = args.temperature
    
    
    if len(args.load_tools) and not len(assistant.tools):
        tools = []
        for cat in args.load_tools:
            tools = get_tools(cat.strip().lower())
            
            tool_by_name = Tool.get_by_name(cat.strip().lower())

            if tool_by_name:
                tools.append(tool_by_name)
    
        assistant.tools = tools
    
    session = await Session.get_by_agent_id(assistant.id)
    if not session:
        session = Session(agent_id=assistant.id)
        
    if args.clear_history:
        session.chat = []
        await session.save()
        
    assistant.verbose = args.verbose
    # print(assistant.formatted_system_prompt())
    
    await assistant.save()
    
    if args.generate:
        await session(args.generate, assistant, stream=args.stream)
            
    elif args.ui.lower() == 'web':
        # start_worker()
        await start_web_ui(assistant)
    else:
        # start_worker()
        await initialize(session, assistant, stream=args.stream)
    # except KeyboardInterrupt:
    #     print("\nExiting...")
    #     sys.exit(1)            
    # except Exception as e:
    #     logging.exception(e)
    #     # parser.print_help()
    #     sys.exit(1)

def get_arguments():
    global parser
    
    try:
        subparsers = parser.add_subparsers()
        agents_parser = subparsers.add_parser('agents', help="Manage agents")
        agents_parser.add_argument("name", type=str, nargs="?", help="Name of an agent to manage (details|update|remove)")  
        agents_parser.add_argument('--new','--create', action='store_true', help='Create a new agent')
        agents_parser.add_argument('-l', '--list', action='store_false', help='List all saved agents')
        agents_parser.add_argument('--update', action='store_true', help='Update an agent')
        agents_parser.add_argument('--delete', action='store_true', help='Delete an agent')
        agents_parser.add_argument('--id', nargs='?', help='Specify agent id to update or delete')
        agents_parser.set_defaults(func=manage_agents)
        
        agents_parser = subparsers.add_parser('tools', help="Manage tools")
        agents_parser.add_argument('-l', '--list', type=str, default='all', nargs='?', choices=['all', 'general', 'system', 'web'], help='List tools by category')
        agents_parser.set_defaults(func=manage_tools)

        parser.add_argument('--name', type=str, default='Assistant', help='Set name of agent')
        parser.add_argument('--provider', default='google', help='Set llm provider to use')
        parser.add_argument('--providers', action='store_true', help='Get a list of all supported providers')
        parser.add_argument('--agents', action='store_true', help='List all saved agents')
        parser.add_argument('--tasks', action='store_true', help='List all saved tasks')
        parser.add_argument('--teams', action='store_true', help='List all saved teams')
        parser.add_argument('--ui', default='cli', help='Determine preferred user interface')
        parser.add_argument('--agent', type=str, default='Assistant', help='Set which saved agent to use')
        parser.add_argument('--load-tools', type=lambda s: [i for i in s.split(',')], default='all', help='Add tools by categories to agent')
        parser.add_argument('--model', type=str, default='', help='Specify model or model_url to use')
        parser.add_argument('--api-key', type=str, default='', help='Set api key of selected llm')
        parser.add_argument('--api-base', type=str, default='', help='Set api base of selected llm. Set if using local llm.')
        parser.add_argument('--temperature', type=float, default=0.1, help='Set temperature of model')
        parser.add_argument('--system-prompt', type=str_or_file, default='', help='Set system prompt of model. Can be a string or a text file path')
        parser.add_argument('--prompt-template', type=str_or_file, default='', help='Set prompt template of model. Can be a string or a text file path')
        parser.add_argument('--generate', type=str, default='', help='Prompt the agent to generate text and then exit after printing out the response.')
        parser.add_argument('--audio', action='store_true', help='Get input from microphone')
        parser.add_argument('--stream', action='store_true', help='Enable response stream')
        parser.add_argument('--session', type=str, default="", help='Load saved session')
        parser.add_argument('--clear-history', action='store_true', default="", help='Clear agent history')
        parser.add_argument('--sessions', action='store_true', help='Get a list of all saved sessions')
        parser.add_argument('--verbose', action='store_true', help='Set verbose mode')
        parser.add_argument('-v','--version', action='version', version=f'%(prog)s {VERSION}')
        parser.set_defaults(func=start)
        
        return parser.parse_args()
    except Exception as e:
        logging.exception(e)
        parser.print_help()
        sys.exit(1)

def start_worker():
    try:
        # Assuming your Celery app is defined in cognitrix.celery_app
        celery_args = ['celery', '-A', 'cognitrix.celery_worker', 'worker', '--loglevel=info']
        worker_process = subprocess.Popen(celery_args)
        print("Celery worker started.")
        return worker_process
    except Exception as e:
        print(f"Error starting Celery worker: {e}")
        return None

def main():
    global parser
    try:
        args = get_arguments()

        # check if args.func is a coroutine
        if asyncio.iscoroutinefunction(args.func):
            asyncio.run(args.func(args))
        else:
            args.func(args)


    except Exception as e:
        logging.exception(e)
        parser.print_help()
        sys.exit(1)
