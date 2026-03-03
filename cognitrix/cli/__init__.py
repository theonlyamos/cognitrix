# CLI Module for Cognitrix
# Modern modular CLI package

from .args import get_arguments
from .core import start
from .handlers import (
    add_agent,
    delete_agent,
    list_agents,
    list_sessions,
    list_tasks,
    list_teams,
    list_tools,
    manage_agents,
    manage_tools,
    start_worker,
)
from .main import main
from .shell import CognitrixCompleter, initialize_shell
from .ui import prompt_agent, start_web_ui
from .utils import create_rich_table, print_table, str_or_file

__all__ = [
    # Main entry points
    'main', 'get_arguments', 'start',
    # Handlers
    'list_agents', 'add_agent', 'delete_agent', 'manage_agents',
    'list_tasks', 'list_teams', 'list_tools', 'manage_tools',
    'list_sessions', 'start_worker',
    # Shell and UI
    'initialize_shell', 'CognitrixCompleter', 'start_web_ui', 'prompt_agent',
    # Utilities
    'print_table', 'str_or_file', 'create_rich_table'
]
