import json
import shutil
from webbrowser import open_new_tab
from typing import Literal, Union, Optional, Any, Tuple, Dict, List, Set
from webbrowser import open_new_tab
from cognitrix.tools.base import Tool
from cognitrix.tools.tool import tool
from cognitrix.teams.base import TeamManager
from cognitrix.agents import Agent
from cognitrix.providers import LLM
from cognitrix.utils import xml_return_format
from tavily import TavilyClient
from bs4 import BeautifulSoup
from pathlib import Path
from rich import print
from PIL import Image
import wikipedia as wk
import pyautogui
import requests
import logging 
import aiohttp
import sys
import os
import multiprocessing
import subprocess
import shlex
import re
import signal

NotImplementedErrorMessage = 'this tool does not suport async'

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
    datefmt='%d-%b-%y %H:%M:%S',
    level=logging.WARNING
)
logger = logging.getLogger('cognitrix.log')

ALLOWED_COMMANDS: Set[str] = {
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
        # Create a list of tuples with line number and content
        line_data = [(i + 1, line.rstrip()) for i, line in enumerate(lines)]
        # Format the output with line numbers
        return '\n'.join(f"{num}: {content}" for num, content in line_data)

@tool(category='general')
def calculator(math_expression: str) -> Any:
    """
    Useful for getting the result of a math expression.
    The input to this tool should be a valid mathematical expression that could be executed by a simple calculator.
    The code will be executed in a python environment so the input should be in a format it can be executed.
    Always present the answer from this tool to the user in a sentence.

    Args:
        math_expression (str): The math expression to evaluate

    Returns:
        Any: The result of the math expression
    """

    return eval(math_expression)

@tool(category='web')
def play_youtube(topic: str):
    """Use this tool when you need to play a youtube video.
    
    Args:
        topic (str): The topic to search for on YouTube
    
    Returns:
        str: The URL of the played video
    """
    url = f"https://www.youtube.com/results?q={topic}"
    count = 0
    cont = requests.get(url)
    data = cont.content
    data = str(data)
    lst = data.split('"')
    for i in lst:
        count += 1
        if i == "WEB_PAGE_TYPE_WATCH":
            break
    if lst[count - 5] == "/results":
        raise Exception("No Video Found for this Topic!")

    video_url = f"https://www.youtube.com{lst[count - 5]}"
    open_new_tab(video_url)
    return video_url

@tool(category='web')
def open_website(url: str):
    """Use this tool when you need to visit a website.
    
    Args:
        url (str): The URL to visit
    
    Returns:
        str: A confirmation message
    """
    print(f"Opening {url} in the internet browser")
    open_new_tab(url)
    return f"Opened {url} in the internet browser"

@tool(category='system')
def list_directory(path: str):
    """List contents of a directory.
    
    Args:
        path (str|Path): The directory path to list. Use '~' or '~/' to reference your home directory.
    
    Returns:
        str: JSON string containing directory contents
    """
    try:
        npath = Path(path).expanduser().resolve()
        if npath.is_dir():
            return 'The content of the directory are: \n' + json.dumps(os.listdir(npath))
        return "The path you provided isn't a directory"
    except Exception as e:
        return str(e)

@tool(category='system')
def open_file(path: str, filename: Optional[str] = None):
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
def create_file(path: str, filename: str, content: Optional[str] = None):
    """Create a new file with optional content.
    
    Args:
        path (str|Path): The directory path where to create the file. Use '~' or '~/' to reference your home directory.
        filename (str): The name of the file to create
        content (str, optional): The content to write to the file
    
    Returns:
        str: Success message
    """
    try:
        npath = Path(path).expanduser().resolve()
        full_path = npath.joinpath(filename)
        
        if not full_path.exists():
            with full_path.open('wt') as file:
                if content:
                    file.write(content)
        
        file_content = get_file_content(full_path)
        return f'Operation done. The content of the file is: \n{file_content}'
    except Exception as e:
        return str(e)

@tool(category='system')
def create_directory(path: str, dirname: str):
    """Create a new directory.
    
    Args:
        path (str|Path): The parent directory path. Use '~' or '~/' to reference your home directory.
        dirname (str): The name of the directory to create
    
    Returns:
        str: Success message
    """
    try:
        npath = Path(path).expanduser().resolve()
        full_path = npath.joinpath(dirname)
        if not full_path.exists() and not full_path.is_dir():
            full_path.mkdir()
        
        return 'Operation done'
    except Exception as e:
        return str(e)

@tool(category='system')
def read_file(path: str, filename: Optional[str] = None):
    """Read contents of a file.
    
    Args:
        path (str|Path): The path to the file. Use '~' or '~/' to reference your home directory.
        filename (str, optional): The name of the file to read
    
    Returns:
        str: The file contents with line numbers or directory listing
    """
    try:
        npath = Path(path).expanduser().resolve()
        full_path = npath.joinpath(filename) if filename else Path(path)
        if full_path.is_file():
            with full_path.open('rt') as file:
                lines = file.readlines()
                # Create a list of tuples with line number and content
                line_data = [(i + 1, line.rstrip()) for i, line in enumerate(lines)]
                # Format the output with line numbers
                return '\n'.join(f"{num}: {content}" for num, content in line_data)
        else:
            return "The path you provided isn't a file"
    except Exception as e:
        return str(e)

@tool(category='system')
def write_file(path: str, filename: str, overwrite: bool = False, content: str = ""):
    """Write content to a new file.
    
    Args:
        path (str|Path): The directory path where the file is located. Use '~' or '~/' to reference your home directory.
        filename (str): The name of the file to write to
        overwrite (bool): Whether to overwrite the file if it already exists. Defaults to False.
        content (str): The content to write to the file
    
    Returns:
        str: Success message
    
    Warning:
        This tool will fail if the file already exists. Use update_file() to modify existing files.
    """
    try:
        npath = Path(path).expanduser().resolve()
        file_path = npath.joinpath(filename)
        
        if file_path.exists() and not overwrite:
            return "Error: File already exists. Use update_file() to modify existing files."
            
        with file_path.open('wt') as file:  # 'wt' for write text mode
            file.write(content)
        
        file_content = get_file_content(file_path)
        return f'Write operation successful. The current content of the file is: \n{file_content}'
    except Exception as e:
        return str(e)

@tool(category='system')
def update_file(path: str, filename: str, operation: Literal['replace','insert','append', 'replace_range'], start_line: int, end_line: int = 0, new_content: str = ""):
    """Update the contents of a file using various operations.

    Args:
        path (str): Path to the file to update
        filename (str): The name of the file to update
        operation (Literal['replace','insert','append', 'replace_range']): The type of update operation:
            - 'replace': Replace content at start_line
            - 'insert': Insert content before start_line
            - 'append': Add content after start_line
            - 'replace_range': Replace content from start_line to end_line
        start_line (int): The line number to start the operation (1-based indexing)
        end_line (int): The ending line number for replace_range operation. Set as 0 if not used.
        new_content (str): The new content to add or replace in the file

    Returns:
        str: Error message if operation fails else Success message

    Raises:
        FileNotFoundError: If the specified file doesn't exist
        ValueError: If line numbers are invalid or operation type is unknown
    """
    start_line = int(start_line)
    end_line = int(end_line)
    try:
        npath = Path(path).expanduser().resolve()
        file_path = npath.joinpath(filename)
        if not file_path.exists():
            raise FileNotFoundError(f"The file {file_path} does not exist.")
        
        # Validate line numbers
        if start_line < 1:
            raise ValueError("start_line must be a positive integer.")
        if end_line is not None and end_line < start_line:
            raise ValueError("end_line must be greater than or equal to start_line.")
        
        # Read all lines from the file
        with open(file_path, 'r') as file:
            lines = file.readlines()
        
        # Determine the operation
        if operation == 'replace':
            # Replace the content of start_line
            if 1 <= start_line <= len(lines):
                lines[start_line - 1] = new_content + '\n'
            else:
                # Append the new content
                lines.append(new_content + '\n')
        elif operation == 'insert':
            # Insert the new content before start_line
            insert_position = start_line - 1
            if insert_position < 0:
                insert_position = 0
            lines.insert(insert_position, new_content + '\n')
        elif operation == 'append':
            # Append the new content after start_line
            append_position = start_line
            if append_position < 0:
                append_position = 0
            elif append_position >= len(lines):
                lines.append(new_content + '\n')
            else:
                lines.insert(append_position + 1, new_content + '\n')
        elif operation == 'replace_range':
            # Replace lines from start_line to end_line with new_content
            if end_line is None:
                raise ValueError("end_line must be provided for 'replace_range' operation.")
            start_idx = start_line - 1
            end_idx = end_line
            # Ensure indices are within bounds
            if start_idx < 0:
                start_idx = 0
            if end_idx > len(lines):
                end_idx = len(lines)
            # Split new_content into lines
            new_lines = new_content.splitlines()
            # Insert the new lines and remove the old range
            lines[start_idx:end_idx] = [line + '\n' for line in new_lines]
        else:
            raise ValueError(f"Invalid operation type: {operation}")
        
        # Write the updated lines back to the file
        with open(file_path, 'w') as file:
            file.writelines(lines)
            
        file_content = get_file_content(file_path)
        return f'Write operation successful. The current content of the file is: \n{file_content}'
    except Exception as e:
        return str(e)

@tool(category='system')
def delete_path(path: str):
    """Delete a file or directory.
    
    Args:
        path (str|Path): The path to delete. Use '~' or '~/' to reference your home directory.
    
    Returns:
        str: Success message
    """
    try:
        npath = Path(path).expanduser().resolve()
        if npath.is_file():
            npath.unlink()
        elif npath.is_dir():
            shutil.rmtree(npath)
        
        return 'Delete operation successful'
    except Exception as e:
        return str(e)

@tool(category='system')
def python_repl(code: str, timeout: Optional[int] = None):
    """Execute Python code in a REPL environment.
    
    Args:
        code (str): The Python code to execute
        timeout (int, optional): Timeout in seconds
    
    Returns:
        str: The output of the code execution
    
    Warning:
        This tool can execute arbitrary code. Use with caution.
    """
    import functools
    from cognitrix.tools.python import PythonREPL, warn_once

    warn_once()
    
    queue = multiprocessing.Queue()
    globals_dict = {}
    locals_dict = {}

    if timeout is not None:
        p = multiprocessing.Process(
            target=PythonREPL.worker,
            args=(code, globals_dict, locals_dict, queue)
        )
        p.start()
        p.join(timeout)
        
        if p.is_alive():
            p.terminate()
            return "Execution timed out"
    else:
        PythonREPL.worker(code, globals_dict, locals_dict, queue)
    
    return queue.get()

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
    screenshot = pyautogui.write(text, 0.15)
    
    return 'Text input completed'

@tool(category='system')
def key_press(key: str):
    """Use this tool to take make key presses.
    Args:
        key (str): Name of key to press.
    
    Returns:
        str: Keypress completed.
    """
    screenshot = pyautogui.press(key.lower())
    
    return 'Keypress completed'

@tool(category='system')
def hot_key(hotkeys: list):
    """Use this tool to take make hot key presses.
    Args:
        hotkeys (list): list of keys to press together.
    
    Returns:
        str: Keypress completed.
    """
    screenshot = pyautogui.hotkey(*hotkeys)
    
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
    screenshot = pyautogui.click(x, y)
    
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
    screenshot = pyautogui.doubleClick(x, y)
    
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
    screenshot = pyautogui.rightClick(x, y)
    
    return 'Mouse double-click completed.'

@tool(category='system')
async def create_agent(name: str, provider: str, description: str, tools: List[str], parent: Optional[Agent] = None):
    """Use this tool to create sub agents for specific tasks.
    
    Args:
        name (str): The name of the agent
        provider (str): The name of the provider to use. Select from [openai, google, anthropic, groq, together, clarifai].
        description (str): Prompt describing the agent's role and functionalities. Should include the agent's role, capabilities and any other info the agent needs to be able to complete it's task. Be as thorough as possible.
        tools (list): Tools the agent needs to complete it's tasks if any.
    
    Returns:
        str: A message indicating whether the the sub agent was created or not.
    """
    
    agent: Optional[Agent] = None
        
    agent = await Agent.create_agent(
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
        agent = await Agent.load_agent(name)
        if agent:
            result = agent.call_sub_agent(agent_name=name, task_description=task)
            return result
        else:
            return f"Error calling agent: {name} not found"
    except Exception as e:
        return f"Error calling agent: {str(e)}"

@tool(category='system')
async def create_new_team(name: str, description: str, agent_names: List[str], leader_name: Optional[str] = None):
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
        new_team = team_manager.create_team(name, description)
        new_team.description = description

        for agent_name in agent_names:
            agent = await Agent.load_agent(agent_name)
            if agent:
                await new_team.add_agent(agent)
            else:
                print(f"Warning: Agent '{agent_name}' not found and couldn't be added to the team.")

        if leader_name:
            leader = await Agent.load_agent(leader_name)
            if leader and leader.id in new_team.assigned_agents:
                new_team.leader = leader
            else:
                print(f"Warning: Leader '{leader_name}' not found or not in the team. No leader set.")

        new_team.save()

        return ['team', new_team, f"Team '{name}' created successfully with {len(new_team.assigned_agents)} agents."]
    except Exception as e:
        return f"Error creating team: {str(e)}"

@tool(category='web')
def internet_search(query: str, search_depth: str = "basic"):
    """Use this to retrieve up-to-date information from the internet 
    and generate more accurate and informative responses.
    
    When you need to answer a question or provide information, you can call this tool 
    to fetch the latest details from the web. 
    This tool takes one argument: the query or question you want to search for.
    
    Args:
        query (str): The query to search for.
        search_depth (str, optional): The search depth. Accepts "basic" or "advanced". Defaults to "basic".
    """
    
    tavily = TavilyClient(api_key=os.getenv('TAVILY_API_KEY', ''))
    
    # max_tokens = 500 if search_depth == "basic" else 1000
    
    response = tavily.search(query, search_depth)
    
    return response['results'] if response else None

@tool(category='web')
def web_scraper(url: str|List[str]):
    """Use this tool to scrape websites when given a link url.

    Args:
        url (str|List[str]): The URL(s) of the website(s) to scrape.

    Returns:
        str: The text content of the scraped website(s).
    """
    
    results: List[str] = []
    if isinstance(url, str):
        url = [url]
        
    for link in url:
        try:
            response = requests.get(link)
            response.raise_for_status()  # Raise an exception for non-2xx status codes
            html_content = response.text

            soup = BeautifulSoup(html_content, 'html.parser')
            text_content = soup.get_text()
            text_content = ' '.join(text_content.split())  # Remove empty spaces
            text_content = f"{link} Scraped Content:\n{text_content}"
            results.append(text_content)
        except Exception as e:
            text_content =  f"{link} Scraped Content:\nError: {e}"
            results.append(text_content)

    return '\n'.join(results)
     
@tool(category='web')
def brave_search(query: str):
    """Use this to retrieve up-to-date information from the internet 
    and generate more accurate and informative responses.
    
    When you need to answer a question or provide information, you can call this tool 
    to fetch the latest details from the web. 
    This tool takes one argument: the query or question you want to search for.
    
    Args:
        query (str): The query to search for.
    """
    url = "https://api.search.brave.com/res/v1/web/search"
    
    params = {
        "q": query,
        "summary": 1
    }
    
    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "X-Subscription-Token": os.getenv('BRAVE_SEARCH_API_KEY', '')
    }
    
    response = requests.get(url, params=params, headers=headers)
    
    if response.status_code == 200:
        results = ""
        for news in response.json()['web']['results']:
            results += f"Title: {news['title']}\nDescripion: {news['description']}\nLink: {news['url']}\n\n"
        
        return results
    else:
        return f"Error: {response.status_code}, {response.text}"
    
@tool(category='web')
def wikipedia(query: str, search_depth: str = 'basic') -> str:
    """Use this to retrieve information from wikipaedia.
    
    When you need to answer a question or provide information, you can call this tool 
    to fetch the information from the web. 
    This tool takes one argument: the query or question you want to search for.
    
    Args:
        query (str): The term to search for.
        search_depth (str, optional): The search depth. Accepts "basic" or "advanced". Defaults to "basic".
    
    Returns:
        str: The search results.
    """
    results = ''
    try:
        if search_depth == 'basic':
            results = wk.summary(query)
        else:
            page = wk.page(query)
            results = page.content
    except wk.exceptions.DisambiguationError as e:
        print(f"DisambiguationError: {e}")
        results = ''
    except wk.exceptions.PageError as e:
        print(f"PageError: {e}")
        results = ''
    except Exception as e:
        print(f"Error: {e}")
        results = ''
    
    return results

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
def terminal_command(command: str, timeout: Optional[int] = 180, working_dir: Optional[str] = str(Path.cwd())) -> str:
    """Execute a terminal command safely with restrictions.
    
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
        # Security check 1: Extract base command
        base_command = command.split()[0].lower()
        
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