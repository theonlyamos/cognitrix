"""
CLI argument parsing and configuration.
"""
import argparse

from .handlers import manage_agents, manage_tools, reset_password
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
    skills_parser.add_argument('args', nargs='*', help='Arguments for --run (positional)')
    skills_parser.add_argument('--arg', action='append', dest='skill_args', default=[],
                             help='Skill arguments as key=value (can be repeated, e.g., --arg file_path=doc.pdf --arg depth=short)')
    skills_parser.set_defaults(func=manage_skills)

    # Forgotten password: whoever can run commands on this machine can set a new one.
    reset_parser = subparsers.add_parser(
        'reset-password', help="Set a new password for an account (for a forgotten password)")
    reset_parser.add_argument('email', type=str, help='Email address of the account')
    reset_parser.add_argument('--password', type=str, default='',
                              help='The new password. Leave out to be asked for it.')
    reset_parser.set_defaults(func=reset_password)

    # App settings from the command line (servers, Docker Compose, recovery).
    from .handlers_settings import add_parser as add_settings_parser

    add_settings_parser(subparsers)

    # Editors that speak the Agent Client Protocol (Zed, JetBrains, ...) start
    # this and talk to it over stdin/stdout (cognitrix/acp/server.py).
    from cognitrix.acp import command as acp_command

    acp_parser = subparsers.add_parser(
        'acp', help="Run as an Agent Client Protocol server for code editors (Zed, JetBrains, ...)")
    acp_parser.add_argument('--agent', dest='acp_agent', type=str, default='',
                            help='Agent to start with, by name (default: your main assistant); the editor can switch agents')
    acp_parser.add_argument('--user', type=str, default='',
                            help="Email of the account whose agents and sign-ins to use (default: this install's owner)")
    acp_parser.set_defaults(func=acp_command)

    # Pair this PC with a web server so agents there can work on it (cognitrix/computer/paired).
    from cognitrix.computer.paired.cli import add_parser as add_connect_parser

    add_connect_parser(subparsers)

    # The message board from any agent that can run a command (cognitrix/board/cli.py).
    from cognitrix.board.cli import add_parser as add_board_parser

    add_board_parser(subparsers)

    # Swarm evals: a set of jobs run with and without workers, scored by a judge model.
    from cognitrix.swarms.evals import command as swarm_evals

    evals_parser = subparsers.add_parser(
        'swarm-evals', help="Measure swarms on a set of jobs (makes real model calls)")
    evals_parser.add_argument('email', type=str, help='Email address of the account to run as')
    evals_parser.add_argument('--agent', type=str, default='',
                              help="The agent that leads, by name (default: the account's main assistant)")
    evals_parser.add_argument('--jobs', type=str, default='all',
                              help='A number, or job ids or kinds (list, parts, best_of, small), comma separated')
    evals_parser.add_argument('--baseline', action='store_true', help='Also run each job without workers')
    evals_parser.add_argument('--timeout', type=float, default=1200, help='Seconds allowed per job')
    evals_parser.add_argument('--out', type=str, default='', help='Save the full results to this JSON file')
    evals_parser.set_defaults(func=swarm_evals)

    # Main arguments
    parser.add_argument('--name', type=str, default='Assistant', help='Set name of agent')
    parser.add_argument('--provider', default='',
                        help="LLM provider for this run (default: the agent's own). Keys and addresses are set in "
                             'Settings > Models and keys')
    parser.add_argument('--agents', action='store_true', help='List all saved agents')
    parser.add_argument('--tasks', action='store_true', help='List all saved tasks')
    parser.add_argument('--teams', action='store_true', help='List all saved teams')
    parser.add_argument('--ui', default='cli', help='Determine preferred user interface')
    parser.add_argument('--agent', type=str, default='',
                        help='Saved agent to use, by name (default: your main assistant)')
    parser.add_argument('--user', type=str, default='',
                        help="Email of the account whose main assistant to use (default: this install's owner)")
    parser.add_argument('--add-dir', dest='add_dirs', action='append', default=[], metavar='PATH',
                        help='Another folder the agent may read and change, besides the current one (repeatable)')
    parser.add_argument('--add-read-dir', dest='add_read_dirs', action='append', default=[], metavar='PATH',
                        help='Another folder the agent may only read (repeatable)')
    parser.add_argument('--load-tools', type=lambda s: [i for i in s.split(',') if i.strip()],
                       default=None, help="Add tools by categories or names for this run (default: the agent's own)")
    parser.add_argument('--model', type=str, default='', help='Specify model or model_url to use')
    parser.add_argument('--api-key', type=str, default='', help='Set api key of selected llm')
    parser.add_argument('--api-base', type=str, default='',
                       help='Override provider base_url (e.g. for local Ollama).')
    parser.add_argument('--temperature', type=float, default=None,
                        help="Override model temperature (default: the agent's own)")
    parser.add_argument('--max-tokens', type=int, default=0, help='Override max tokens (default 4096, 0=use default)')
    parser.add_argument('--system-prompt', type=str_or_file, default='',
                       help='Set system prompt of model. Can be a string or a text file path')
    parser.add_argument('--prompt-template', type=str_or_file, default='',
                       help='Set prompt template of model. Can be a string or a text file path')
    parser.add_argument('--prompt', '-p', type=str, default='',
                       help='Prompt the agent to generate text and then exit after printing out the response.')
    parser.add_argument('--task', action='store_true',
                       help='Run the prompt as a planned multi-step task instead of a chat turn.')
    parser.add_argument('--continue', '-c', dest='continue_session', action='store_true',
                       help='With --prompt, continue the previous session instead of starting a new one.')
    parser.add_argument('--prompt-file', type=str, default='',
                       help='Read the prompt from a file instead of the command line. Use this for long, '
                            'multi-line, or special-character prompts that the shell would mangle. Takes '
                            'precedence over --prompt/-p.')
    parser.add_argument('--audio', action='store_true', help='Get input from microphone')
    parser.add_argument('--stream', action=argparse.BooleanOptionalAction, default=True,
                       help='Stream responses token-by-token (default: on; use --no-stream to disable)')
    parser.add_argument('--session', type=str, default="", help='Load saved session')
    parser.add_argument('--clear-history', action='store_true', default="", help='Clear agent history')
    parser.add_argument('--sessions', action='store_true', help='Get a list of all saved sessions')
    parser.add_argument('--verbose', action='store_true', help='Set verbose mode')
    parser.add_argument('--dangerously-skip-permissions', action='store_true',
                       help='Run fully unattended: auto-approve every tool call AND let the '
                            'bash tool run any command through a real shell (no whitelist). '
                            'ONLY use inside a throwaway sandbox/container — it removes the '
                            'safety gate and command restrictions entirely.')
    parser.add_argument('-v','--version', action='version', version=f'%(prog)s {VERSION}')

    # Import start function for default action
    from .core import start
    parser.set_defaults(func=start)

    return parser.parse_args()
