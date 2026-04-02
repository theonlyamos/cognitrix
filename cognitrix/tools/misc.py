from __future__ import annotations

import json
import logging
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Literal, TYPE_CHECKING
from webbrowser import open_new_tab

import pyautogui
import requests
import wikipedia as wk
from bs4 import BeautifulSoup
from rich import print
from tavily import TavilyClient

from cognitrix.config import settings
from cognitrix.tools.tool import tool

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
    datefmt='%d-%b-%y %H:%M:%S',
    level=logging.WARNING
)
logger = logging.getLogger('cognitrix.log')

ALLOWED_COMMANDS: set[str] = {
    # File system navigation
    'ls', 'dir', 'pwd',
    # File operations
    'cat', 'head', 'tail', 'less', 'more',
    # System info
    'date', 'whoami', 'hostname', 'uname',
    # Process info
    'ps', 'top',
    # Network
    'ping', 'netstat',
    # Package management
    'pip', 'pip3',
    # Git operations
    'git',
    # Directory operations
    'cd', 'mkdir', 'rmdir'
}

def get_file_content(full_path: Path):
    with full_path.open('rt') as file:
        lines = file.readlines()
        line_data = [(i + 1, line.rstrip()) for i, line in enumerate(lines)]
        return '\n'.join(f"{num}: {content}" for num, content in line_data)


@tool(category='system')
def open_file(path: str, filename: str | None = None):
    """Open a file or folder using the system's default application.

    Args:
        path (str|Path): The path to the file or folder. Use '~' or '~/' to reference your home directory.
        filename (str, optional): The name of the file to open

    Returns:
        str: Success or error message
    """
    try:
        npath = Path(path).expanduser().resolve()
        if filename and npath.joinpath(filename).exists():
            os.startfile(npath.joinpath(filename))
            return 'Successfully opened file'
        elif os.path.exists(npath):
            os.startfile(npath)
            return 'Successfully opened folder'
        return 'Unable to open file'
    except Exception as e:
        return str(e)


@tool(category='system')
def take_screenshot():
    """Use this tool to take a screenshot of the screen."""
    screenshot = pyautogui.screenshot()

    return ['image', screenshot]

@tool(category='system')
def text_input(text: str):
    """Use this tool to take make text inputs.
    Args:
        text (str): Text to input.

    Returns:
        str: Text input completed.
    """
    pyautogui.write(text, 0.15)

    return 'Text input completed'

@tool(category='system')
def key_press(key: str):
    """Use this tool to take make key presses.
    Args:
        key (str): Name of key to press.

    Returns:
        str: Keypress completed.
    """
    pyautogui.press(key.lower())

    return 'Keypress completed'

@tool(category='system')
def hot_key(hotkeys: list):
    """Use this tool to take make hot key presses.
    Args:
        hotkeys (list): list of keys to press together.

    Returns:
        str: Keypress completed.
    """
    pyautogui.hotkey(*hotkeys)

    return 'Keypress completed'

@tool(category='system')
def mouse_click(x: int, y: int):
    """Use this tool to take make mouse clicks.
    Args:
        x (int): X coordinate of mouse click.
        y (int): y coordinate of mouse click.

    Returns:
        str: Mouse click completed.
    """
    pyautogui.click(x, y)

    return 'Mouse Click completed'

@tool(category='system')
def mouse_double_click(x: int, y: int):
    """Use this tool to take make mouse double clicks.
    Args:
        x (int): X coordinate of mouse click.
        y (int): y coordinate of mouse click.

    Returns:
        str: Mouse double-click completed.
    """
    pyautogui.doubleClick(x, y)

    return 'Mouse double-click completed.'

@tool(category='system')
def mouse_right_click(x: int, y: int):
    """Use this tool to take make mouse right clicks.
    Args:
        x (int): X coordinate of mouse click.
        y (int): y coordinate of mouse click.

    Returns:
        str: Mouse right-click completed.
    """
    pyautogui.rightClick(x, y)

    return 'Mouse double-click completed.'

@tool(category='system')
async def create_agent(name: str, provider: str, description: str, tools: list[str], parent: Any | None = None):
    """Use this tool to create sub agents for specific tasks.

    Args:
        name (str): The name of the agent
        provider (str): The name of the provider to use. Select from [openai, google, anthropic, groq, together, clarifai].
        description (str): Prompt describing the agent's role and functionalities. Should include the agent's role, capabilities and any other info the agent needs to be able to complete it's task. Be as thorough as possible.
        tools (list): Tools the agent needs to complete it's tasks if any.

    Returns:
        str: A message indicating whether the the sub agent was created or not.
    """

    # Local import to avoid circular-import issues.
    from cognitrix.agents import Agent  # noqa: WPS433  (allow internal import)

    agent = await Agent.create_agent(  # type: ignore[attr-defined]
        name=name,
        system_prompt=description,
        provider=provider,
        is_sub_agent=True if parent else False,
        parent_id=parent.id if parent else None,
        tools=tools
    )

    if agent:
        agent.system_prompt = description
        await agent.save()
        return {'status': 'success', 'message': f'Agent "{name}" created successfully'}

    return {'status': 'error', 'message': f'Error creating agent "{name}"'}

@tool(category='system')
async def call_agent(name: str, task: str):
    """Run a task with a sub agent

    Args:
        name (str): Name of the agent to call
        task (str): The task|query to perform|answer

    Returns:
        str: The result of the task

    Raises:
        Exception: If the agent is not found or the task fails
    """
    try:
        from cognitrix.agents import Agent  # noqa: WPS433
        agent = await Agent.load_agent(name)  # type: ignore[attr-defined]
        if agent:
            result = agent.call_sub_agent(agent_name=name, task_description=task)
            return result
        else:
            return f"Error calling agent: {name} not found"
    except Exception as e:
        return f"Error calling agent: {str(e)}"

@tool(category='system')
async def create_new_team(name: str, description: str, agent_names: list[str], leader_name: str | None = None):
    """Use this tool to create new teams with existing agents.

    Args:
        name (str): The name of the team
        description (str): A description of the team's purpose and goals
        agent_names (List[str]): List of existing agent names to be added to the team
        leader_name (Optional[str]): Name of an existing agent to be set as the team leader (optional)

    Returns:
        str: A message indicating whether the team was created successfully or not.
    """

    try:
        team_manager = TeamManager()
        from cognitrix.teams.base import TeamManager
        new_team = team_manager.create_team(name, description)
        new_team.description = description

        from cognitrix.agents import Agent  # noqa: WPS433

        for agent_name in agent_names:
            agent = await Agent.load_agent(agent_name)  # type: ignore[attr-defined]
            if agent:
                await new_team.add_agent(agent)
            else:
                print(f"Warning: Agent '{agent_name}' not found and couldn't be added to the team.")

        if leader_name:
            leader = await Agent.load_agent(leader_name)  # type: ignore[attr-defined]
            if leader and leader.id in new_team.assigned_agents:
                new_team.leader = leader
            else:
                print(f"Warning: Leader '{leader_name}' not found or not in the team. No leader set.")

        await new_team.save()

        return ['team', new_team, f"Team '{name}' created successfully with {len(new_team.assigned_agents)} agents."]
    except Exception as e:
        return f"Error creating team: {str(e)}"

# REMOVED - Replaced by skills:
# - internet_search -> internet-search skill
# - web_scraper -> web-scraper skill  
# - brave_search -> brave-search skill
# - wikipedia -> wikipedia skill


@tool(category='system')
def create_tool(name: str, description: str, category: str, function_code: str):
    """Use this tool to create new tools dynamically.

    Args:
        name (str): The name of the new tool.
        description (str): A description of what the tool does and how to use it.
        category (str): The category of the tool (e.g., 'system', 'web', 'general').
        function_code (str): The Python code for the tool's function.

    Returns:
        str: A message indicating whether the tool was created successfully.
    """
    try:
        # Create the function object from the provided code
        exec(function_code)

        # Get the function object
        func = locals()[name.lower().replace(" ", "_")]

        # Create the new tool using the @tool decorator
        new_tool = tool(category=category)(func)

        # Set the description
        new_tool.__doc__ = description

        # Add the new tool to the global namespace
        globals()[name.lower().replace(" ", "_")] = new_tool

        return f"Tool '{name}' created successfully."
    except Exception as e:
        return f"Error creating tool: {str(e)}"

@tool(category='system')
def bash(command: str, timeout: int | None = 180, working_dir: str | None = str(Path.cwd())) -> str:
    """Execute a bash/terminal command safely with restrictions.

    Args:
        command (str): The command to execute. Only whitelisted commands are allowed.
        timeout (int, optional): Maximum execution time in seconds. Defaults to 30.
        working_dir (str, optional): Working directory for command execution. Defaults to current directory.

    Returns:
        str: Command output or error message.

    Warning:
        This tool only allows specific whitelisted commands for security.
        Commands are sanitized before execution.
        Use with caution as it interacts with the system directly.
    """
    try:
        # Convert timeout to int if passed as string (e.g., from LLM)
        if timeout is not None:
            try:
                timeout = int(timeout)
            except (ValueError, TypeError):
                return f"Error: Invalid timeout value '{timeout}'. Must be an integer."

        # Security check 1: Extract base command (for logging/validation)
        base_command = command.split()[0].lower() if command else ""

        # Security check 2: Verify command is whitelisted
        # if base_command not in ALLOWED_COMMANDS:
        #     return f"Error: Command '{base_command}' is not allowed. Allowed commands: {', '.join(sorted(ALLOWED_COMMANDS))}"

        # Security check 3: Sanitize command
        try:
            # Use shlex to safely split command into arguments
            command_parts = shlex.split(command)
        except ValueError as e:
            return f"Error: Invalid command format - {str(e)}"

        # Security check 4: Additional command validation
        for part in command_parts:
            # Check for suspicious patterns
            if re.search(r'[;&|]', part) or '..' in part:
                return "Error: Command contains forbidden characters or patterns"

        # Security check 5: Validate and resolve working directory
        if working_dir:
            try:
                work_dir = Path(working_dir).resolve()
                if not work_dir.exists() or not work_dir.is_dir():
                    return f"Error: Invalid working directory - {working_dir}"
            except Exception as e:
                return f"Error: Working directory validation failed - {str(e)}"
        else:
            work_dir = Path.cwd()

        # Execute command with timeout and capture output
        try:
            process = subprocess.Popen(
                command_parts,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=str(work_dir),
                shell=True
            )

            try:
                stdout, stderr = process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                # Clean up process if it times out
                process.kill()
                return f"Error: Command timed out after {timeout} seconds"

            # if process.returncode != 0:
            #     return f"Command failed with error:\n{stderr}"

            # Security check 6: Sanitize output
            output = stdout.strip()
            # Remove any control characters except newlines and tabs
            output = re.sub(r'[\x00-\x09\x0b-\x1f\x7f-\x9f]', '', output)

            return output if output else "Command executed successfully (no output)"

        except Exception as e:
            print(e)
            return f"Error executing command: {str(e)}"

    except Exception as e:
        return f"Unexpected error: {str(e)}"
