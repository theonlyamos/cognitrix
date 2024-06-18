import os
import sys
import asyncio
import logging
import argparse
from pathlib import Path
from argparse import Namespace
from typing import Optional

from cognitrix.llms import (
    Cohere, Clarifai, LLM
)

from cognitrix.agents import AIAssistant, Agent
from cognitrix.llms.session import Session
from cognitrix.tools import Tool

from cognitrix.config import VERSION

logger = logging.getLogger('cognitrix.log')

def start_web_ui(agent: Agent | AIAssistant):
    from fastapi import WebSocket
    from starlette.websockets import WebSocketDisconnect
    from .api.main import app
    import uvicorn
    
    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        await websocket.accept()
        session = await agent.load_session()
        try:
            agent.WSConnection = websocket
            while True:
                query = await websocket.receive_json()
                
                if query['type'] == 'chat_history':
                    await websocket.send_json({'type': 'chat_history', 'content': session.chat})
                elif query['type'] == 'sessions':
                    sessions = [sess.dict() for sess in Session.list_sessions()]
                    await websocket.send_json({'type': 'sessions', 'content': sessions})
                else:
                    response = await agent.chat(query, session)
                    await websocket.send_json({'type': 'chat_reply', 'content': response})
        except WebSocketDisconnect:
            logger.warning('Websocket disconnected')
            agent.WSConnection = None
        except Exception as e:
            logger.exception(e)
            agent.WSConnection = None
    
    uvicorn.run(app, forwarded_allow_ips="*")

def add_agent():
    new_agent = asyncio.run(Agent.create_agent()) # type: ignore
    if new_agent:
        description = input("\n[Enter agent system prompt]: ")
        if description:
            new_agent.prompt_template = description
            asyncio.run(new_agent.save())
        print(f"\nAgent **{new_agent.name}** added successfully!")
    else:
        print("\nError creating agent")
    sys.exit()

def list_agents():
    agents = asyncio.run(Agent.list_agents())
    agent_names = [agent.name for agent in agents]
    max_col = len(max(agent_names, key=len))
    max_width = 10
    max_col = max_col if max_col >= max_width else max_width
    
    print(max_col)
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
    provider_names = [p for p in providers]
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
        print(f"| {str(index) + ' '*padding}| {p + ' '*(max_col-len(p))} |")

        
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
        if args.list:
            list_agents()
    except KeyboardInterrupt:
        print()
        sys.exit()
    except Exception as e:
        logging.exception(e)
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
            provider = LLM.load_llm(model_name=args.provider)
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
            loaded_agent = Agent.load_agent(args.agent)
            
            if loaded_agent:
                assistant = loaded_agent
            else:
                # assistant_description = "You are an ai assistant. Your main goal is to help the user complete tasks"
                assistant = asyncio.run(AIAssistant.create_agent(name=args.agent, llm=provider)) #type: ignore

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
                    loaded_tools = Tool.get_tools_by_category(cat.lower())
                    tools.extend(loaded_tools)

                assistant.tools = tools
            
            assistant.format_system_prompt()
            asyncio.run(assistant.save())

            if not args.web_ui:
                assistant.start(args.session, audio=args.audio)
            else:
                start_web_ui(assistant)
                
    except Exception as e:
        logging.exception(e)
        parser.print_help()
        sys.exit(1)

def get_arguments():
    global parser
    
    subparsers = parser.add_subparsers()
    agents_parser = subparsers.add_parser('agents', help="Manage agents")
    agents_parser.add_argument("name", type=str, nargs="?", help="Name of an agent to manage (details|update|remove)")  
    agents_parser.add_argument('--new', action='store_true', help='Create a new agent')
    agents_parser.add_argument('-l', '--list', action='store_false', help='List all saved agents')
    agents_parser.add_argument('--update', action='store_true', help='Update an agent')
    agents_parser.add_argument('--remove', action='store_true', help='Delete an agent')
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
    parser.add_argument('--load-tools', type=lambda s: [i for i in s.split(',')], default='general', help='Add tools by categories to agent')
    parser.add_argument('--model', type=str, default='', help='Specify model or model_url to use')
    parser.add_argument('--api-key', type=str, default='', help='Set api key of selected llm')
    parser.add_argument('--api-base', type=str, default='', help='Set api base of selected llm. Set if using local llm.')
    parser.add_argument('--temperature', type=float, default=0.1, help='Set temperature of model')
    parser.add_argument('--system-prompt', type=str_or_file, default='', help='Set system prompt of model. Can be a string or a text file path')
    parser.add_argument('--prompt-template', type=str_or_file, default='', help='Set prompt template of model. Can be a string or a text file path')
    parser.add_argument('--audio', action='store_true', help='Get input from microphone')
    parser.add_argument('--session', type=str, default="", help='Load saved session')
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
