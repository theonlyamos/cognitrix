"""
CLI argument parsing and configuration.
"""
import argparse

from .handlers import manage_agents, manage_tools
from .handlers_skills import manage_skills
from .utils import str_or_file


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

    # Skills subcommand
    skills_parser = subparsers.add_parser('skills', help="Manage skills")
    skills_parser.add_argument('name', type=str, nargs='?', help='Skill name')
    skills_parser.add_argument('-l', '--list', action='store_true', help='List installed skills')
    skills_parser.add_argument('--show', type=str, help='Show skill details')
    skills_parser.add_argument('--create', action='store_true', help='Create a new skill')
    skills_parser.add_argument('--install', type=str, help='Install from path/URL/registry')
    skills_parser.add_argument('--remove', type=str, help='Remove a skill')
    skills_parser.add_argument('--validate', type=str, help='Validate a SKILL.md')
    skills_parser.add_argument('--run', type=str, help='Run a skill by name')
    skills_parser.add_argument('--search', type=str, help='Search for skills')
    skills_parser.add_argument('args', nargs='*', help='Arguments for --run')
    skills_parser.set_defaults(func=manage_skills)

    # Main arguments
    parser.add_argument('--name', type=str, default='Assistant', help='Set name of agent')
    parser.add_argument('--provider', default='openrouter', help='LLM provider. Config from env: AI_PROVIDER, *_BASE_URL, *_API_KEY, *_MODEL')
    parser.add_argument('--agents', action='store_true', help='List all saved agents')
    parser.add_argument('--tasks', action='store_true', help='List all saved tasks')
    parser.add_argument('--teams', action='store_true', help='List all saved teams')
    parser.add_argument('--ui', default='cli', help='Determine preferred user interface')
    parser.add_argument('--agent', type=str, default='Assistant', help='Set which saved agent to use')
    parser.add_argument('--load-tools', type=lambda s: [i for i in s.split(',')],
                       default='all', help='Add tools by categories to agent')
    parser.add_argument('--model', type=str, default='google/gemini-3.1-flash-lite-preview', help='Specify model or model_url to use')
    parser.add_argument('--api-key', type=str, default='', help='Set api key of selected llm')
    parser.add_argument('--api-base', type=str, default='',
                       help='Override provider base_url (e.g. for local Ollama).')
    parser.add_argument('--temperature', type=float, default=0.4, help='Override model temperature (default 0.4)')
    parser.add_argument('--max-tokens', type=int, default=0, help='Override max tokens (default 4096, 0=use default)')
    parser.add_argument('--system-prompt', type=str_or_file, default='',
                       help='Set system prompt of model. Can be a string or a text file path')
    parser.add_argument('--prompt-template', type=str_or_file, default='',
                       help='Set prompt template of model. Can be a string or a text file path')
    parser.add_argument('--generate', type=str, default='',
                       help='Prompt the agent to generate text and then exit after printing out the response.')
    parser.add_argument('--audio', action='store_true', help='Get input from microphone')
    parser.add_argument('--stream', type=bool, default=True, help='Enable response stream')
    parser.add_argument('--session', type=str, default="", help='Load saved session')
    parser.add_argument('--clear-history', action='store_true', default="", help='Clear agent history')
    parser.add_argument('--sessions', action='store_true', help='Get a list of all saved sessions')
    parser.add_argument('--verbose', action='store_true', help='Set verbose mode')
    parser.add_argument('-v','--version', action='version', version=f'%(prog)s {VERSION}')

    # Import start function for default action
    from .core import start
    parser.set_defaults(func=start)

    return parser.parse_args()
