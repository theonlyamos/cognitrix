# CLI Module for Cognitrix
# Modern modular CLI package

from .main import main
from .args import get_arguments
from .core import start
from .handlers import (
    list_agents, add_agent, delete_agent, manage_agents,
    list_tasks, list_teams, list_tools, manage_tools,
    list_providers, list_sessions, start_worker
)
from .shell import initialize_shell, CognitrixCompleter
from .ui import start_web_ui, prompt_agent
from .utils import print_table, str_or_file, create_rich_table

__all__ = [
    # Main entry points
    'main', 'get_arguments', 'start',
    # Handlers
    'list_agents', 'add_agent', 'delete_agent', 'manage_agents',
    'list_tasks', 'list_teams', 'list_tools', 'manage_tools', 
    'list_providers', 'list_sessions', 'start_worker',
    # Shell and UI
    'initialize_shell', 'CognitrixCompleter', 'start_web_ui', 'prompt_agent',
    # Utilities
    'print_table', 'str_or_file', 'create_rich_table'
] 