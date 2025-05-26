"""
CLI argument parsing and configuration.
"""
import argparse
from .utils import str_or_file
from .handlers import manage_agents, manage_tools


def get_arguments():
    """Parse and return command line arguments."""
    from cognitrix.config import VERSION
    
    parser = argparse.ArgumentParser(description="Build and run AI agents on your computer")
    subparsers = parser.add_subparsers()
    
    # Agents subcommand
    agents_parser = subparsers.add_parser('agents', help="Manage agents")
    agents_parser.add_argument("name", type=str, nargs="?", help="Name of an agent to manage (details|update|remove)")
    agents_parser.add_argument('--new','--create', action='store_true', help='Create a new agent')
    agents_parser.add_argument('-l', '--list', action='store_true', help='List all saved agents')
    agents_parser.add_argument('--update', action='store_true', help='Update an agent')
    agents_parser.add_argument('--delete', action='store_true', help='Delete an agent')
    agents_parser.add_argument('--id', nargs='?', help='Specify agent id to update or delete')
    agents_parser.set_defaults(func=manage_agents)
    
    # Tools subcommand
    tools_parser = subparsers.add_parser('tools', help="Manage tools")
    tools_parser.add_argument('-l', '--list', type=str, default='all', nargs='?', 
                            choices=['all', 'general', 'system', 'web'], 
                            help='List tools by category')
    tools_parser.set_defaults(func=manage_tools)
    
    # Main arguments
    parser.add_argument('--name', type=str, default='Assistant', help='Set name of agent')
    parser.add_argument('--provider', default='groq', help='Set llm provider to use')
    parser.add_argument('--providers', action='store_true', help='Get a list of all supported providers')
    parser.add_argument('--agents', action='store_true', help='List all saved agents')
    parser.add_argument('--tasks', action='store_true', help='List all saved tasks')
    parser.add_argument('--teams', action='store_true', help='List all saved teams')
    parser.add_argument('--ui', default='cli', help='Determine preferred user interface')
    parser.add_argument('--agent', type=str, default='Assistant', help='Set which saved agent to use')
    parser.add_argument('--load-tools', type=lambda s: [i for i in s.split(',')], 
                       default='all', help='Add tools by categories to agent')
    parser.add_argument('--model', type=str, default='', help='Specify model or model_url to use')
    parser.add_argument('--api-key', type=str, default='', help='Set api key of selected llm')
    parser.add_argument('--api-base', type=str, default='', 
                       help='Set api base of selected llm. Set if using local llm.')
    parser.add_argument('--temperature', type=float, default=0.1, help='Set temperature of model')
    parser.add_argument('--system-prompt', type=str_or_file, default='', 
                       help='Set system prompt of model. Can be a string or a text file path')
    parser.add_argument('--prompt-template', type=str_or_file, default='', 
                       help='Set prompt template of model. Can be a string or a text file path')
    parser.add_argument('--generate', type=str, default='', 
                       help='Prompt the agent to generate text and then exit after printing out the response.')
    parser.add_argument('--audio', action='store_true', help='Get input from microphone')
    parser.add_argument('--stream', action='store_true', help='Enable response stream')
    parser.add_argument('--session', type=str, default="", help='Load saved session')
    parser.add_argument('--clear-history', action='store_true', default="", help='Clear agent history')
    parser.add_argument('--sessions', action='store_true', help='Get a list of all saved sessions')
    parser.add_argument('--verbose', action='store_true', help='Set verbose mode')
    parser.add_argument('-v','--version', action='version', version=f'%(prog)s {VERSION}')
    
    # Import start function for default action
    from .core import start
    parser.set_defaults(func=start)
    
    return parser.parse_args() 