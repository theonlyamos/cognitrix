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

@tool(category='general')
def calculator(math_expression: str):
    """
    Useful for getting the result of a math expression.
    The input to this tool should be a valid mathematical expression that could be executed by a simple calculator.
    The code will be executed in a python environment so the input should be in a format it can be executed.
    Always present the answer from this tool to the user in a sentence.

    Example:
        User: what is the square root of 25?
        Assistant: <observation>The user is asking for the square root of 25 and has specified this should use the Calculator tool.</observation>
            <mindspace>
        Mathematical: Square root operation, perfect squares
        Educational: Basic algebra, exponents and roots
        Computational: Calculator functionality, numeric operations
        Practical: Real-world applications of square roots
            </mindspace>
            <thought>Step 1) The question asks for the square root of 25.
        Step 2) We need to use the Calculator tool to compute this.
        Step 3) The Calculator tool accepts a math_expression as an argument.
        Step 4) The correct expression for square root in most calculators is "sqrt(25)".</thought>
            <type>tool_call</type>
            <tool_call>
                <name>Calculator</name>
                <arguments>
                    <math_expression>sqrt(25)</math_expression>
                </arguments>
            </tool_call>
        

    :param math_expression: The math expression to evaluate
    """

    return eval(math_expression)

@tool(category='web')
def play_youtube(topic: str):
    """Use this tool when you need to play a youtube video.
    
    Args:
        topic (str): The topic to search for on YouTube
    
    Returns:
        str: The URL of the played video
    
    Example:
        User: Play a video about cats
        AI Assistant: 
            <observation>The user wants to watch a YouTube video about cats.</observation>
            <mindspace>
            Video Content: Cat-related videos, pet content
            User Experience: Video playback, browser interaction
            Search: YouTube search algorithms, relevant results
            Media Platform: YouTube functionality, video streaming
            </mindspace>
            <thought>Step 1) I'll use the Play YouTube tool to search for and play a video about cats.
            Step 2) The tool will search YouTube and open the most relevant video in a new browser tab.</thought>
            <type>tool_call</type>
            <tool_call>
                <name>Play Youtube</name>
                <arguments>
                    <topic>cats</topic>
                </arguments>
            </tool_call>
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
    
    Example:
        User: Visit https://example.com
        AI Assistant: 
            <observation>The user wants to open a webpage at https://example.com</observation>
            <mindspace>
            Web Browsing: URL validation, webpage loading
            User Interface: Browser interaction, new tab creation
            Internet: Web protocols, domain resolution
            Security: URL safety, HTTPS verification
            </mindspace>
            <thought>Step 1) I'll use the Open Website tool to open the specified URL.
            Step 2) The tool will open the URL in a new browser tab.</thought>
            <type>tool_call</type>
            <tool_call>
                <name>Open Website</name>
                <arguments>
                    <url>https://example.com</url>
                </arguments>
            </tool_call>
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
    
    Example:
        # List contents of home directory
        list_directory('~')
        
        # List contents of Documents folder in home directory
        list_directory('~/Documents')
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
    
    Example:
        # Open a specific file in home directory
        open_file('~', 'document.txt')
        
        # Open a directory
        open_file('~/Documents')
        
        # Open a file using full path
        open_file('~/Documents/report.pdf')
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
    
    Example:
        # Create empty file in home directory
        create_file('~', 'newfile.txt')
        
        # Create file with content in Documents folder
        create_file('~/Documents', 'notes.txt', 'Hello World!')
        
        # Create file in current directory with content
        create_file('.', 'config.json', '{"setting": "value"}')
    """
    try:
        npath = Path(path).expanduser().resolve()
        full_path = npath.joinpath(filename)
        
        if not full_path.exists():
            with full_path.open('wt') as file:
                if content:
                    file.write(content)
        
        return 'Operation done'
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
    
    Example:
        # Create directory in home folder
        create_directory('~', 'new_folder')
        
        # Create directory in Documents
        create_directory('~/Documents', 'project_files')
        
        # Create directory in current location
        create_directory('.', 'temp')
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
    
    Example:
        # Read file from home directory
        read_file('~', 'document.txt')
        
        # Read file using full path
        read_file('~/Documents/notes.txt')
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
def write_file(path: str, filename: str, content: str):
    """Write content to a new file.
    
    Args:
        path (str|Path): The directory path where the file is located. Use '~' or '~/' to reference your home directory.
        filename (str): The name of the file to write to
        content (str): The content to write to the file
    
    Returns:
        str: Success message
    
    Example:
        # Create new file in home directory
        write_file('~', 'notes.txt', 'Hello World!')
        
        # Create new file in Documents folder
        write_file('~/Documents', 'config.json', '{"setting": "value"}')
    
    Warning:
        This tool will fail if the file already exists. Use update_file() to modify existing files.
    """
    try:
        npath = Path(path).expanduser().resolve()
        file_path = npath.joinpath(filename)
        
        if file_path.exists():
            return "Error: File already exists. Use update_file() to modify existing files."
            
        with file_path.open('wt') as file:  # 'wt' for write text mode
            file.write(content)
        
        return 'Write operation successful.'
    except Exception as e:
        return str(e)

@tool(category='system')
def update_file(path: str, filename: str, new_content: str, operation: Literal['replace','insert','append', 'replace_range'], start_line: int, end_line: Optional[int] = None):
    """Update the contents of a file using various operations.

    Args:
        path (str): Path to the file to update
        filename (str): The name of the file to update
        new_content (str): The new content to add or replace in the file
        operation (Literal['replace','insert','append', 'replace_range']): The type of update operation:
            - 'replace': Replace content at start_line
            - 'insert': Insert content before start_line
            - 'append': Add content after start_line
            - 'replace_range': Replace content from start_line to end_line
        start_line (int): The line number to start the operation (1-based indexing)
        end_line (Optional[int]): The ending line number for replace_range operation

    Returns:
        str: Error message if operation fails else Success message

    Examples:
        # Replace a line in a Python script
        update_file('~/projects', 'main.py', 'def main():', 'replace', 10)

        # Insert a new import at the start of a file
        update_file('~/config', 'settings.json', 'import logging', 'insert', 1)

        # Append a new entry to a configuration file
        update_file('~/docker', 'docker-compose.yml', '  redis:\n    image: redis:latest', 'append', 15)

        # Replace a block of HTML content
        update_file('~/website', 'index.html', '''<div class="header">
            <h1>Welcome</h1>
            <p>This is my website</p>
        </div>''', 'replace_range', 5, 8)

    Raises:
        FileNotFoundError: If the specified file doesn't exist
        ValueError: If line numbers are invalid or operation type is unknown
    """
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
        
        return 'Write operation successful.'
    except Exception as e:
        return str(e)

@tool(category='system')
def delete_path(path: str):
    """Delete a file or directory.
    
    Args:
        path (str|Path): The path to delete. Use '~' or '~/' to reference your home directory.
    
    Returns:
        str: Success message
    
    Example:
        # Delete file from home directory
        delete_path('~/unwanted.txt')
        
        # Delete directory and its contents
        delete_path('~/old_project')
        
        # Delete file from Documents
        delete_path('~/Documents/temp.txt')
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
    """Use this tool to take a screenshot of the screen.
    
    Usage Example:
    
    User: take a screenshot
    AI Assistant: 
        <observation>I need to take a screenshot of the user's screen.</observation>
        <mindspace>
    Screen Capture: Screenshot techniques, image formats
    User Interface: Current screen contents, visual information
    Privacy: Potentially sensitive information on screen
    System Interaction: Screen capture permissions, system API usage
        </mindspace>
        <thought>Step 1) To take a screenshot, I need to use the Take Screenshot tool.
    Step 2) Calling the Take Screenshot tool.</thought>
        <type>tool_call</type>
        <tool_call>
            <name>Take Screenshot</name>
            <arguments></arguments>
        </tool_call>
    
    """
    screenshot = pyautogui.screenshot()
    
    return ['image', screenshot]

@tool(category='system')
def text_input(text: str):
    """Use this tool to take make text inputs.
    Args:
        text (str): Text to input.
    
    Returns:
        str: Text input completed.
    
    Example Usage:
    
    User: write hello world
    AI Assistant: 
        <observation>I need to input text on the computer using the keyboard.</observation>
        <mindspace>
    User Interface: Keyboard input, text entry fields
    Human-Computer Interaction: Simulating user typing
    System Control: Programmatic text input
    Application Focus: Active window or text field for input
        </mindspace>
        <thought>Step 1) To perform this action, I will need to use the Text Input tool.
    Step 2) The Text Input function takes one argument: text (a string representing the text to input).
    Step 3) Calling the Text Input function with argument: 'hello world'.</thought>
        <type>tool_call</type>
        <tool_call>
            <name>Text Input</name>
            <arguments>
                <text>hello world</text>
            </arguments>
        </tool_call>
    
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
    
    Usage Example:
    
    User: Press windows key
    AI Assistant: 
        <observation>I need to perform a single key press on the computer.</observation>
        <mindspace>
    User Interface: Keyboard input, key press simulation
    System Interaction: Keyboard input, key press functionality
    Human-Computer Interaction: Simulating user keyboard input
    Application Focus: Active application, keyboard input
        </mindspace>
        <thought>Step 1) This action requires the usage of the Key Press tool.
    Step 2) The Key Press tool takes one argument: key (a string representing the name of the key to press).
    Step 3) Calling the Key Press tool with argument: 'win'.</thought>
        <type>tool_call</type>
        <tool_call>
            <name>Key Press</name>
            <arguments>
                <key>win</key>
            </arguments>
        </tool_call>
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
    
    Example:
        User: Make a paste
        AI Assistant: 
            <observation>I need to perform a paste action using hotkey combination on the computer.</observation>
            <mindspace>
    User Interface: Keyboard input, hotkey functionality
    System Interaction: Hotkey simulation, keyboard input
    Human-Computer Interaction: Simulating user keyboard input
    Application Focus: Active application, keyboard input
        </mindspace>
        <thought>Step 1) I need to use the Hot Key tool to perform this action.
    Step 2) The Hot Key tool takes a variable number of arguments (*hotkeys), which should be a list of keys to press together as a hotkey combination.
    Step 3) Calling the Hot Key tool with list of keys to press together.</thought>
        <type>tool_call</type>
        <tool_call>
            <name>Hot Key</name>
            <arguments>
                <hotkeys>ctrl</hotkeys>
                <hotkeys>v</hotkeys>
            </arguments>
        </tool_call>
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
    
    Example:
        User: Click on Brave icon
        AI Assistant: 
            <observation>I need to perform a mouse click at specific coordinates on the screen.</observation>
            <mindspace>
        Visual: Desktop interface, Brave browser icon location
        Spatial: Coordinate system on computer screen
        Technical: Mouse input simulation, click functionality
        User Interface: Icon recognition, mouse click activation
        </mindspace>
        <thought>Step 1) To perform the mouse-click, I need to use the Mouse Click tool.
        Step 2) The Mouse Click tool takes two arguments: x (the x-coordinate of the mouse click) and y (the y-coordinate of the mouse click).
        Step 3) Calling the Mouse Click tool with the x,y coordinates</thought>
        <type>tool_call</type>
        <tool_call>
            <name>Mouse Click</name>
            <arguments>
                <x>123</x>
                <y>456</y>
            </arguments>
        </tool_call>
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
    
    Example:
        User: Click on Brave icon
        AI Assistant: 
            <observation>I need to perform a mouse double-click at specific coordinates on the screen.</observation>
            <mindspace>
                Visual: Desktop interface, Brave browser icon location
                Spatial: Coordinate system on computer screen
                Technical: Mouse input simulation, double-click functionality
                User Interface: Icon recognition, mouse double-click activation
            </mindspace>
            <thought>Step 1) To perform the mouse double-click, I need to use the Mouse Double Click tool.
            Step 2) The Mouse Double Click tool takes two arguments: x (the x-coordinate of the mouse click) and y (the y-coordinate of the mouse click).
            Step 3) Calling the Mouse Double Click tool with the x,y coordinates</thought>
            <type>tool_call</type>
            <tool_call>
                <name>Mouse Double Click</name>
                <arguments>
                    <x>123</x>
                    <y>456</y>
                </arguments>
            </tool_call>
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
    
    Example:
        User: Right-click on Brave icon
        AI Assistant: 
            <observation>I need to perform a mouse right-click at specific coordinates on the screen.</observation>
            <mindspace>
            Visual: Desktop interface, Brave browser icon location
            Spatial: Coordinate system on computer screen
            Technical: Mouse input simulation, right-click functionality
            User Interface: Icon recognition, context menu activation
            </mindspace>
            <thought>Step 1) To perform the mouse right-click, I need to use the Mouse Right Click tool.
        Step 2) The Mouse Click tool takes two arguments: x (the x-coordinate of the mouse click) and y (the y-coordinate of the mouse click).
        Step 3) Calling the Mouse Right Click tool with the x,y coordinates</thought>
            <type>tool_call</type>
            <tool_call>
                <name>Mouse Right Click</name>
                <arguments>
                    <x>123</x>
                    <y>456</y>
                </arguments>
            </tool_call>
    """
    screenshot = pyautogui.rightClick(x, y)
    
    return 'Mouse double-click completed.'

@tool(category='system')
async def create_new_agent(name: str, llm: str, description: str, tools: List[str], parent: Agent):
    """Use this tool to create sub agents for specific tasks.
    
    Args:
        name (str): The name of the agent
        llm (str): The name of the llm to use. Select from [openai, google, anthropic, groq, together, clarifai].
        description (str): Prompt describing the agent's role and functionalities. Should include the agent's role, capabilities and any other info the agent needs to be able to complete it's task. Be as thorough as possible.
        tools (list): Tools the agent needs to complete it's tasks if any.
    
    Returns:
        str: A message indicating whether the the sub agent was created or not.
    
    Example:
        User: Create sub agent for a task
        AI Assistant: 
            <observation>The user has requested me to create a sub-agent for a specific task.</observation>
            <mindspace>
        Agent Creation: Sub-agent design, task specialization
        Natural Language Processing: LLM selection, prompt engineering
        Task Delegation: Workload distribution, specialized processing
        System Architecture: Multi-agent systems, modular AI design
            </mindspace>
            <thought>Step 1) To create the sub agent, I need to use the Create Sub Agent Tool.
        Step 2) The Create Sub Agent tool takes four main arguments: name (the name of the sub-agent), llm (the name of the language model to use), description (a prompt describing the agent's role and capabilities), and tools (a list of tools the agent can use).
        Step 3) In this case, the user hasn't provided specific details for the sub-agent, so I'll use placeholder values.
        Step 4) Calling the Create Sub Agent Tool with required arguments.</thought>
            <type>tool_call</type>
            <tool_call>
                <name>Create Sub Agent</name>
                <arguments>
                    <name>&lt;agent_name&gt;</name>
                    <llm>&lt;llm&gt;</llm>
                    <description>&lt;agent_prompt&gt;</description>
                    <tools></tools>
                </arguments>
            </tool_call>
        
        
        User: Create the snake game in python
        AI Assistant: 
            <observation>The user has requested me to create the classic Snake game in Python.</observation>
            <mindspace>
        Game Development: Snake game mechanics, Python game libraries
        Programming: Python syntax, object-oriented design
        User Interface: Game graphics, user input handling
        Agent Creation: Sub-agent specialization, task delegation
        Software Engineering: Code modularity, best practices
            </mindspace>
            <thought>Step 1) To create the Snake game in Python, I will need to create a specialized sub-agent called 'CodeWizard' with expertise in Python programming.
        Step 2) I will provide the CodeWizard agent with the instructions to write the code for the Snake game in Python, following the specified return format.
        Step 3) I will delegate the task of writing the Python code for the Snake game to the CodeWizard agent.</thought>
            <type>tool_call</type>
            <tool_call>
                <name>Create Sub Agent</name>
                <arguments>
                    <name>CodeWizard</name>
                    <llm>gemini</llm>
                    <description>You are a skilled Python programmer tasked with creating the classic Snake game. Your role is to write clean, efficient, and well-documented code that implements the game's logic, user interface, and any additional features you deem necessary. You should follow best practices for software development and ensure your code is modular, readable, and maintainable.</description>
                    <tools>
                        File System Browser
                    </tools>
                    <tools>
                        Call Sub Agentool
                    </tools>
                </arguments>
            </tool_call>
        
    """
    
    sub_agent: Optional[Agent] = None
        
    description += '\n\n{tools}'
    
    if not "return_format" in description:
        description += f"\n{xml_return_format}"
        
    sub_agent = await Agent.create_agent(
        name=name,
        system_prompt=description,
        provider=llm,
        is_sub_agent=True,
        parent_id=parent.id,
        tools=tools
    )
        
    if sub_agent:
        sub_agent.system_prompt = description
        sub_agent.save()
        return ['agent', sub_agent, 'Sub agent created successfully']
    
    return "Error creating sub agent"

@tool(category='system')
def call_agent(name: str, task: str, parent: Agent):
    """Run a task with a sub agent
    
    Args:
        name (str): Name of the agent to call
        task (str): The task|query to perform|answer
    
    Example:
        User: Run task with sub agent
        
        AI Assistant: 
            <observation>I need to run a task with a sub-agent.</observation>
            <mindspace>
        Task Delegation: Sub-agent capabilities, task decomposition
        Programming: Python coding, game development
        Agent Collaboration: Inter-agent communication, task handoff
        Efficiency: Parallel processing, specialized skills utilization
            </mindspace>
            <thought>Step 1) This can be accomplished by using the Call Sub Agent Tool.
        Step 2) The Call Sub Agent tool takes two arguments: name (the name of the sub-agent to call) and task (the task or query for the sub-agent to perform or answer).
        Step 3) Calling the Call Sub Agent tool with required arguments.</thought>
            <type>tool_call</type>
            <tool_call>
                <name>Call Sub Agent</name>
                <arguments>
                    <name>CodeWizard</name>
                    <task>Write the code for the Snake game in Python.</task>
                </arguments>
            </tool_call>
        
    """
    parent.call_sub_agent(name, task)
    
    return "Agent running"

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
    
    Example:
        User: Create a new research team
        AI Assistant: 
            <observation>The user has requested to create a new research team with existing agents.</observation>
            <mindspace>
        Team Management: Team creation, role assignment
        Collaboration: Group dynamics, task distribution
        Project Planning: Team objectives, resource allocation
        Leadership: Team leader selection, responsibility delegation
            </mindspace>
            <thought>Step 1) To create a new team, I need to use the Create New Team tool.
        Step 2) The Create New Team tool takes four arguments: name (the name of the team), description (the team's purpose and goals), agent_names (a list of existing agent names to add to the team), and leader_name (optional, the name of an existing agent to be the team leader).
        Step 3) I'll use the provided information to create the team.
        Step 4) Calling the Create New Team tool with the required arguments.</thought>
            <type>tool_call</type>
            <tool_call>
                <name>Create New Team</name>
                <arguments>
                    <name>Research Team Alpha</name>
                    <description>A team dedicated to conducting cutting-edge research in artificial intelligence and machine learning.</description>
                    <agent_names>
                        Research Assistant
                    </agent_names>
                    <agent_names>
                        Data Analyst
                    </agent_names>
                    <agent_names>
                        Domain Expert
                    </agent_names>
                    <leader_name>Alice</leader_name>
                </arguments>
            </tool_call>
    """
    
    try:
        team_manager = TeamManager()
        new_team = team_manager.create_team(name, description)
        new_team.description = description

        for agent_name in agent_names:
            agent = await Agent.load_agent(agent_name)
            if agent:
                new_team.add_agent(agent)
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
    
    Example:
        User: who is the president of the United States?
        AI Assistant: 
            <observation>I need to search the internet for information about the president of the United States.</observation>
            <mindspace>
        Political Information: Current world leaders, US government structure
        Internet Search: Search engine algorithms, query formulation
        Information Retrieval: Credible sources, fact-checking
        Current Events: Recent elections, political changes
            </mindspace>
            <thought>Step 1) To do an internet search, I need to use the Internet Search tool.
        Step 2) The Internet Search tool takes two arguments: query (the topic to search for) and search_depth (the search depth, either "basic" or "advanced").
        Step 3) Calling the Internet Search tool with the provided query.</thought>
            <type>tool_call</type>
            <tool_call>
                <name>Internet Search</name>
                <arguments>
                    <query>who is the president of the United States?</query>
                    <search_depth>basic</search_depth>
                </arguments>
            </tool_call>
    """
    
    tavily = TavilyClient(api_key=os.getenv('TAVILY_API_KEY', ''))
    
    # max_tokens = 500 if search_depth == "basic" else 1000
    
    response = tavily.search(query, search_depth)
    
    return response['results'] if response else None

@tool(category='web')
def web_scraper(url: str|List[str]):
    """Use this tool to scrape websites when given a link url.

    Args:
        url (str): The URL of the website to scrape.

    Returns:
        str: The text content of the scraped website.

    Example:
        User: Scrape the website https://example.com
        AI Assistant: 
            <observation>The user has requested to scrape a website.</observation>
            <mindspace>
        Web Scraping: HTML parsing, data extraction techniques
        Internet: Website structure, HTTP requests
        Data Analysis: Processing scraped information
        Legal and Ethical Considerations: Website terms of service, data usage rights
            </mindspace>
            <thought>Step 1) To scrape a website, I need to use the Web Scraper tool.
        Step 2) The Web Scraper tool takes one argument: url (the URL of the website to scrape).
        Step 3) Calling the Web Scraper tool with the provided URL.</thought>
            <type>tool_call</type>
            <tool_call>
                <name>Web Scraper</name>
                <arguments>
                    <url>https://example.com</url>
                </arguments>
            </tool_call>
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
    
    Example:
        User: who is the president of the United States?
        AI Assistant: 
            <observation>I need to search the internet for information about the president of the United States.</observation>
            <mindspace>
        Political Information: Current world leaders, US government structure
        Internet Search: Search engine algorithms, query formulation
        Information Retrieval: Credible sources, fact-checking
        Current Events: Recent elections, political changes
            </mindspace>
            <thought>Step 1) To do an internet search, I need to use the Internet Search tool.
        Step 2) The Brave Search tool takes one argument: query (the topic to search for).
        Step 3) Calling the Brave Search tool with the provided query.</thought>
            <type>tool_call</type>
            <tool_call>
                <name>Brave Search</name>
                <arguments>
                    <query>who is the president of the United States?</query>
                </arguments>
            </tool_call>
        
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
def wikipedia(query: str, search_depth: str = 'basic'):
    """Use this to retrieve information from wikipaedia.
    
    When you need to answer a question or provide information, you can call this tool 
    to fetch the information from the web. 
    This tool takes one argument: the query or question you want to search for.
    
    Args:
    query (str): The term to search for.
    search_depth (str, optional): The search depth. Accepts "basic" or "advanced". Defaults to "basic".
    
    Returns:
    str: The search results.
    
    Example:
    User: who is Joe Biden?
    AI Assistant: 
        <observation>I need to search the internet for information about Joe Biden</observation>
        <mindspace>
    Political Information: Current world leaders, US government structure
    Internet Search: Search engine algorithms, query formulation
    Information Retrieval: Credible sources, fact-checking
    Current Events: Recent elections, political changes
        </mindspace>
        <thought>Step 1) To do an internet search, I need to use the Wikipedia tool.
    Step 2) The Wikipaedia tool takes two arguments: query (the topic to search for) and search_depth (the search depth, either "basic" or "advanced").
    Step 3) Calling the Wikipaedia tool with the provided query.</thought>
        <type>tool_call</type>
        <tool_call>
            <name>Wikipedia</name>
            <arguments>
                <query>Joe Biden</query>
                <search_depth>basic</search_depth>
            </arguments>
        </tool_call>
    
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
    
    Example:
        User: Create a new tool that reverses a string
        AI Assistant: 
            <observation>The user wants to create a new tool for reversing strings.</observation>
            <mindspace>
            Tool Creation: Dynamic function generation, code evaluation
            Python Programming: String manipulation, function definition
            System Integration: Adding new tools to the existing set
            Safety: Code execution risks, input validation
            </mindspace>
            <thought>Step 1) To create a new tool, I need to use the Create Tool function.
            Step 2) I'll define the tool's name, description, category, and function code.
            Step 3) The function code should reverse a given string.
            Step 4) Calling the Create Tool function with the necessary arguments.</thought>
            <type>tool_call</type>
            <tool_call>
                <name>Create Tool</name>
                <arguments>
                    <name>Reverse String</name>
                    <description>This tool reverses a given string.
                    Args:
                        text (str): The string to reverse.
                    Returns:
                        str: The reversed string.</description>
                    <category>general</category>
                    <function_code>
def reverse_string(text: str) -> str:
return text[::-1]
                    </function_code>
                </arguments>
            </tool_call>
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
    
    Example:
        User: List files in current directory
        AI Assistant: 
            <observation>I need to list files in the current directory using a terminal command.</observation>
            <mindspace>
            File System: Directory structure, file listing
            Terminal: Command execution, output formatting
            Security: Safe command execution, permissions
            System Integration: Command line interface, process management
            </mindspace>
            <thought>Step 1) I'll use the Terminal Command tool to execute 'ls'.
            Step 2) This is a safe, whitelisted command for listing directory contents.
            Step 3) Using default timeout and working directory settings.</thought>
            <type>tool_call</type>
            <tool_call>
                <name>Terminal Command</name>
                <arguments>
                    <command>ls</command>
                </arguments>
            </tool_call>
    
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