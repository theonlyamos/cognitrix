import os
import sys
import asyncio
import logging
import argparse
from pathlib import Path
from argparse import Namespace

from cognitrix.llms import (
    Cohere, OpenAI,LLM
)

from cognitrix.agents import AIAssistant, Agent
from cognitrix.llms.session import Session
from cognitrix.tools import Tool

from cognitrix.config import VERSION

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
    agents_str = "\nAvailable Agents:"
    for index, agent in enumerate(asyncio.run(Agent.list_agents())):
        agents_str += (f"\n[{index}] {agent.name}")                 #type: ignore
    print(agents_str)
    
def list_providers():
    print("\nAvailable providers:")
    for index, l in enumerate(LLM.list_llms()):
        print(f"[{index}] {l}")
        
def list_tools():
    print("\nAvailable Tools:")
    for index, l in enumerate(Tool.list_all_tools()):
        print(f"[{index}] {l.name}")
        
def list_sessions():
    print("\nSaved Sessions:")
    sessions = asyncio.run(Session.list_sessions())
    for index, l in enumerate(sessions):
        print(f"[{index}] {l.id}")

def manage_agents(args: Namespace):
    try:
        if args.new:
            add_agent()
        elif args.list:
            list_agents()
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
        elif args.tools:
            list_tools()
            sys.exit()
        elif args.sessions:
            list_sessions()
            sys.exit()
        
        provider = None
        if args.provider:
            provider = LLM.load_llm(model_name=args.provider)
        provider = provider() if provider else Cohere()
        
        
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
            if args.provider:
                assistant.llm = provider
            assistant.name = args.name
            if args.load_all_tools:
                assistant.tools = Tool.list_all_tools()
            
            assistant.format_system_prompt()
            asyncio.run(assistant.save())
            
            # if args.audio:
            #     AudioTranscriber.transcribe_from_mic(assistant.start_audio)
            # else:
            #     
            assistant.start(args.session)
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
    agents_parser.add_argument('--list', action='store_false', help='List all saved agents')
    agents_parser.add_argument('--update', action='store_true', help='Update an agent')
    agents_parser.add_argument('--remove', action='store_true', help='Delete an agent')
    
    agents_parser.set_defaults(func=manage_agents)

    parser.add_argument('--name', type=str, default='Assistant', help='Set name of agent')
    parser.add_argument('--provider', default='', help='Set llm provider to use')
    parser.add_argument('--providers', action='store_true', help='Get a list of all supported providers')
    parser.add_argument('--agents', action='store_true', help='List all saved agents')
    parser.add_argument('--agent', type=str, default='Assistant', help='Set which saved agent to use')
    parser.add_argument('--tools', action='store_true', help='List all available tools')
    parser.add_argument('--load-all-tools', action='store_true', help='Add all available tools to agent')
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
