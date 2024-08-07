import json
import os
import sys
import asyncio
import logging
import argparse
from pathlib import Path
from argparse import Namespace
from typing import Literal, Optional
from fastapi import Request
from rich import print

from fastapi.responses import JSONResponse

from cognitrix.llms import (
    Cohere, Clarifai, LLM
)

from cognitrix.agents import AIAssistant, Agent
from cognitrix.llms.session import Session
from cognitrix.tools import Tool
from cognitrix.utils.ws import WebSocketManager
from cognitrix.utils.sse import SSEManager
from cognitrix.agents.templates import ASSISTANT_SYSTEM_PROMPT

from cognitrix.config import VERSION
from cognitrix.celery_worker import celery_thread

logger = logging.getLogger('cognitrix.log')

def start_web_ui(agent: Agent | AIAssistant):
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
        await ws_manager.websocket_endpoint(websocket)

    uvicorn.run(app, forwarded_allow_ips="*")
    
def add_agent():
    new_agent = asyncio.run(Agent.create_agent()) # type: ignore
    if new_agent:
        print(f"\nAgent **{new_agent.name}** added successfully!")
    else:
        print("\nError creating agent")
    sys.exit()
    
def delete_agent(agent_name_or_index: str):
    if agent_name_or_index:
        agent_deleted = asyncio.run(Agent.delete(agent_name_or_index))
        if agent_deleted:
            print(f"\nAgent **{agent_name_or_index}** deleted successfully!")
        else:
            print(f"Agent **{agent_name_or_index}** couldn't be deleted")
    else:
        print("\nError deleting agent")
    sys.exit()

def list_agents():
    agents = asyncio.run(Agent.list_agents())
    agent_names = [agent.name for agent in agents]
    max_col = len(max(agent_names, key=len))
    max_width = 10
    max_col = max_col if max_col >= max_width else max_width
    
    print("\nAvailable Agents:")
    print(f" {'_'*((int(len(agents)/10)+5)+max_width+1)}")
    print(f"| #{' '*(int(len(agents)/10))} | {'Agent Name'+' '*(max_col-max_width)} |")
    print(f"|{'-'*(int(len(agents)/10)+3)}|-{'-'*(max_col)}-|")
    for index, a in enumerate(agents):
        padding = (len(str(index)))-int(len(agents)/10)
        padding = padding if padding else padding + 2
        print(f"| {str(index) + ' '*padding}| {a.name + ' '*(max_col-len(a.name))} |")
    
def list_providers():
    providers = LLM.list_llms()
    provider_names = [p.__name__ for p in providers]
    max_col = len(max(provider_names, key=len))
    max_width = 9
    max_col = max_col if max_col >= max_width else max_width

    print("\nAvailable Providers:")
    print(f" {'_'*((int(len(providers)/10)+5)+max_width+1)}")
    print(f"| #{' '*(int(len(providers)/10))} | {'Provider'+' '*(max_col-7)}|")
    print(f"|{'-'*(int(len(providers)/10)+3)}|-{'-'*(max_col)}-|")
    for index, p in enumerate(providers):
        padding = (len(str(index)))-int(len(providers)/10)
        padding = padding if padding else padding + 2
        print(f"| {str(index) + ' '*padding}| {p.__name__ + ' '*(max_col-len(p.__name__))} |")

        
def list_tools(category='all'):
    tools = []
    if category == 'all':
        tools = Tool.list_all_tools()
    else:
        tools = Tool.get_tools_by_category(category)
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
        
def list_sessions():
    print("\nSaved Sessions:")
    sessions = Session.list_sessions()
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

async def prompt_agent(assistant: AIAssistant|Agent, prompt):
    async for response in assistant.generate(prompt):
        if response.text:
            print(f"\r{response.text}", end='')
    print()

async def initialize(session: Session, agent: Agent|AIAssistant, stream: bool = False):
    query: str | dict = input("\nUser (q to quit): ")
    while True:
        try:
            if not query:
                query: str | dict = input("\nUser (q to quit): ")
                continue

            if query.lower() in ['q', 'quit', 'exit']:
                print('Exiting...')
                break

            elif query.lower() == 'add agent':
                new_agent = asyncio.run(Agent.create_agent(is_sub_agent=True, parent_id=agent.id))
                if new_agent:
                    agent.add_sub_agent(new_agent)
                    print(f"\nAgent {new_agent.name} added successfully!")
                else:
                    print("\nError creating agent")

                query = input("\nUser (q to quit): ")
                continue

            elif query.lower() == 'list tools':
                agents_str = "\nAvailable Tools:"
                tools = [tool for tool in agent.tools]
                for index, tool in enumerate(tools):
                    agents_str += (f"\n[{index}] {tool.name}")
                print(agents_str)
                query = input("\nUser (q to quit): ")
                continue
            
            elif query.lower() == 'list agents':
                tools_str = "\nAvailable Agents:"
                sub_agents = [a for a in asyncio.run(Agent.list_agents()) if agent.parent_id == agent.id]
                for index, agent in enumerate(sub_agents):
                    tools_str += (f"\n[{index}] {agent.name}")
                print(tools_str)
                query = input("\nUser (q to quit): ")
                continue
            
            elif query.lower() == 'show history':
                history_str = "\nChat History:"
                history = session.chat
                for index, chat in enumerate(history):
                    history_str += (f"\n[{chat['role']}]: {chat['message']}\n")
                print(history_str)
                query = input("\nUser (q to quit): ")
                continue

            await session(query, agent, streaming=stream)
            query = ''
        
        except KeyboardInterrupt:
            print('Exiting...')
            break
        except Exception as e:
            logger.exception(e)
            break


def start(args: Namespace):
    try:
        if args.providers:
            list_providers()
            sys.exit()
        elif args.agents:
            list_agents()              #type: ignore
            sys.exit()
        elif args.sessions:
            list_sessions()
            sys.exit()
        
        provider = None
        if args.provider:
            provider = LLM.load_llm(provider=args.provider)
        provider = provider() if provider else Clarifai()
        
        
        if args.api_key:
            provider.api_key = args.api_key
        if args.model:
            provider.model = args.model
        
        provider.temperature = args.temperature
        if args.system_prompt:
            provider.system_prompt = args.system_prompt
        # llm = TogetherLLM()
        loaded_agent = None
        if args.agent:
            loaded_agent = asyncio.run(Agent.load_agent(args.agent))
            
            if loaded_agent:
                assistant = loaded_agent
            else:
                # assistant_description = "You are an ai assistant. Your main goal is to help the user complete tasks"
                assistant = asyncio.run(AIAssistant.create_agent(name=args.agent, llm=provider, description=ASSISTANT_SYSTEM_PROMPT))

        else:
            assistant = AIAssistant(llm=provider, name=args.name, verbose=args.verbose)
        
        if assistant:
            assistant.name = args.name

            if args.provider:
                assistant.llm = provider
            if 'all' in args.load_tools:
                assistant.tools = Tool.list_all_tools()
            else:
                tools = []
                for cat in args.load_tools:
                    loaded_tools = Tool.get_tools_by_category(cat.strip().lower())
                    
                    tool_by_name = Tool.get_by_name(cat.strip().lower())

                    if tool_by_name:
                        loaded_tools.append(tool_by_name)
                    tools.extend(loaded_tools)
                
                assistant.tools = tools
            
            session = asyncio.run(Session.get_by_agent_id(assistant.id))
            if args.clear_history:
                session.chat = []
                session.save()
                
            assistant.verbose = args.verbose
            assistant.formatted_system_prompt()
                
            asyncio.run(assistant.save())
            if args.generate:
                asyncio.run(session(args.generate, assistant, streaming=args.stream))
                    
            elif args.web_ui:
                celery_thread.start()
                start_web_ui(assistant)
            else:
                celery_thread.start()
                asyncio.run(initialize(session, assistant, stream=args.stream))
    except KeyboardInterrupt:
        print("\nExiting...")
        sys.exit(1)            
    except Exception as e:
        logging.exception(e)
        parser.print_help()
        sys.exit(1)


def get_arguments():
    global parser
    
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
    parser.add_argument('--provider', default='', help='Set llm provider to use')
    parser.add_argument('--providers', action='store_true', help='Get a list of all supported providers')
    parser.add_argument('--agents', action='store_true', help='List all saved agents')
    parser.add_argument('--web-ui', action='store_true', help='Expose api server')
    parser.add_argument('--agent', type=str, default='Assistant', help='Set which saved agent to use')
    parser.add_argument('--load-tools', type=lambda s: [i for i in s.split(',')], default='', help='Add tools by categories to agent')
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

def main():
    global parser
    try:
        parser = argparse.ArgumentParser(description="Build and run AI agents on your computer")
        args = get_arguments()
        args.func(args)

    except Exception as e:
        logging.exception(e)
        parser.print_help()
        sys.exit(1)

if __name__ == "__main__":
    main()
