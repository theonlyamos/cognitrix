import os
import sys
import logging
import argparse
from pathlib import Path
from argparse import Namespace

from cognitrix.llms import (
    Cohere, OpenAI,LLM
)

from .agents import AIAssistant, Agent
from .tools import (
    Calculator, YoutubePlayer,
    WorldNews, PythonREPL,
    FSBrowser, SearchTool,
    InternetBrowser, take_screenshot,
    text_input, key_press, mouse_click,
    mouse_double_click, mouse_right_click
)

from .config import VERSION

def add_agent():
    new_agent = Agent.create_agent() # type: ignore
    if new_agent:
        print(f"\nAgent {new_agent.name} added successfully!")
    else:
        print("\nError creating agent")
    sys.exit()

def list_agents():
    agents_str = "\nAvailable Agents:"
    for index, agent in enumerate(Agent.list_agents()):
        agents_str += (f"\n[{index}] {agent.name}")                 #type: ignore
    print(agents_str)
    
def list_platforms():
    print("\nAvailable Platforms:")
    for index, l in enumerate(LLM.list_llms()):
        print(f"[{index}] {l}")

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
        logging.warning(str(e))
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
        if args.platforms:
            list_platforms()
            sys.exit()
        elif args.agents:
            list_agents()              #type: ignore
            sys.exit()
        platform = None
        if args.platform:
            platform = LLM.load_llm(model_name=args.platform)
        platform = platform() if platform else Cohere()
        
        if args.api_key:
            platform.api_key = args.api_key
        if args.model:
            platform.model = args.model
        
        platform.temperature = args.temperature
        if args.system_prompt:
            platform.system_prompt = args.system_prompt
        # llm = TogetherLLM()
        loaded_agent = None
        if args.agent:
            loaded_agent = Agent.load_agent(args.agent)
            
            if loaded_agent:
                assistant = loaded_agent
            else:
                assistant_description = "You are an ai assistant. Your main goal is to help the user complete tasks"
                assistant = AIAssistant.create_agent(name=args.agent, task_description=assistant_description, llm=platform) #type: ignore

        else:
            assistant = AIAssistant(llm=platform, name=args.name, verbose=args.verbose)
        
        if assistant:
            assistant.llm = platform
            assistant.name = args.name
            assistant.add_tool(take_screenshot)
            assistant.add_tool(text_input)
            assistant.add_tool(key_press)
            assistant.add_tool(mouse_click)
            assistant.add_tool(mouse_double_click)
            assistant.add_tool(mouse_right_click)
            # assistant.add_tool(Calculator())
            # assistant.add_tool(YoutubePlayer())
            # assistant.add_tool(WorldNews())
            # assistant.add_tool(FSBrowser())
            # assistant.add_tool(PythonREPL())
            # assistant.add_tool(InternetBrowser())
            # assistant.add_tool(SearchTool())
            assistant.start()
    except Exception as e:
        logging.error(str(e))
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

    parser.add_argument('--name', type=str, default='Adam', help='Set name of agent')
    parser.add_argument('--platform', default='', help='Set llm platform to use')
    parser.add_argument('--platforms', action='store_true', help='Get a list of all supported platforms')
    parser.add_argument('--agents', action='store_true', help='List all saved agents')
    parser.add_argument('--agent', type=str, default='Assistant', help='Set which saved agent to use')
    parser.add_argument('--model', type=str, default='', help='Specify model or model_url to use')
    parser.add_argument('--api-key', type=str, default='', help='Set api key of selected llm')
    parser.add_argument('--api-base', type=str, default='', help='Set api base of selected llm. Set if using local llm.')
    parser.add_argument('--temperature', type=float, default=0.1, help='Set temperature of model')
    parser.add_argument('--system-prompt', type=str_or_file, default='', help='Set system prompt of model. Can be a string or a text file path')
    parser.add_argument('--prompt-template', type=str_or_file, default='', help='Set prompt template of model. Can be a string or a text file path')
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
        logging.error(str(e))
        parser.print_help()
        sys.exit(1)

if __name__ == "__main__":
    main()
