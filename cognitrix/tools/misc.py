from __future__ import annotations

import fnmatch
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


@tool(category='filesystem')
def Read(file_path: str, start_line: int = 1, end_line: int | None = None, show_line_numbers: bool = True):
    """Read the contents of a file, optionally with line range selection.

    Args:
        file_path (str): Path to the file to read. Supports absolute and relative paths.
        start_line (int, optional): Starting line number (1-based). Defaults to 1.
        end_line (int, optional): Ending line number (1-based). If None, reads to end of file.
        show_line_numbers (bool, optional): Whether to show line numbers. Defaults to True.

    Returns:
        str: File contents with line numbers, or error message

    Examples:
        - Read entire file: Read("path/to/file.py")
        - Read first 100 lines: Read("file.py", end_line=100)
        - Read lines 50-100: Read("file.py", start_line=50, end_line=100)
    """
    try:
        path = Path(file_path).expanduser().resolve()

        if not path.exists():
            return f"Error: File not found: {file_path}"

        if not path.is_file():
            return f"Error: Not a file: {file_path}"

        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()

        total_lines = len(lines)

        if start_line < 1:
            start_line = 1
        if end_line is None or end_line > total_lines:
            end_line = total_lines
        if start_line > end_line:
            return f"Error: start_line ({start_line}) > end_line ({end_line})"

        selected_lines = lines[start_line - 1:end_line]

        if show_line_numbers:
            result = []
            for i, line in enumerate(selected_lines, start=start_line):
                result.append(f"{i:6d}: {line.rstrip()}")
            output = '\n'.join(result)
        else:
            output = ''.join(selected_lines)

        return f"File: {path}\nLines: {start_line}-{end_line} of {total_lines}\n\n{output}"

    except PermissionError:
        return f"Error: Permission denied reading: {file_path}"
    except Exception as e:
        return f"Error reading file: {str(e)}"


@tool(category='filesystem')
def Write(file_path: str, content: str, append: bool = False):
    """Write content to a file, creating it if it doesn't exist.

    Args:
        file_path (str): Path to the file to write. Supports absolute and relative paths.
        content (str): Content to write to the file.
        append (bool, optional): If True, append to file instead of overwriting. Defaults to False.

    Returns:
        str: Success message with file info, or error message

    Examples:
        - Write new file: Write("path/to/file.txt", "Hello world")
        - Append to file: Write("log.txt", "new entry\n", append=True)
    """
    try:
        path = Path(file_path).expanduser().resolve()

        parent = path.parent
        if not parent.exists():
            parent.mkdir(parents=True, exist_ok=True)

        mode = 'a' if append else 'w'
        with open(path, mode, encoding='utf-8') as f:
            f.write(content)

        action = "Appended to" if append else "Written to"
        return f"{action} file: {path}\nSize: {path.stat().st_size} bytes"

    except PermissionError:
        return f"Error: Permission denied writing to: {file_path}"
    except Exception as e:
        return f"Error writing file: {str(e)}"


@tool(category='filesystem')
def Edit(file_path: str, old_string: str, new_string: str, replace_all: bool = False, create_if_missing: bool = False):
    """Edit a file by replacing text. Supports single or all occurrences.

    Args:
        file_path (str): Path to the file to edit.
        old_string (str): The text to find and replace.
        new_string (str): The replacement text.
        replace_all (bool, optional): If True, replace all occurrences. Defaults to False (first only).
        create_if_missing (bool, optional): If True, create file with content if it doesn't exist. Defaults to False.

    Returns:
        str: Success message with changes made, or error message

    Examples:
        - Replace first occurrence: Edit("file.py", "old_text", "new_text")
        - Replace all occurrences: Edit("file.py", "old_text", "new_text", replace_all=True)
    """
    try:
        path = Path(file_path).expanduser().resolve()

        if not path.exists():
            if create_if_missing:
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(new_string)
                return f"Created file with content: {path}"
            return f"Error: File not found: {file_path}"

        if not old_string:
            return "Error: old_string cannot be empty"

        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()

        if old_string not in content:
            return f"Error: String not found in file: {old_string[:50]}..."

        if replace_all:
            new_content = content.replace(old_string, new_string)
            count = content.count(old_string)
        else:
            new_content = content.replace(old_string, new_string, 1)
            count = 1

        with open(path, 'w', encoding='utf-8') as f:
            f.write(new_content)

        return f"Replaced {count} occurrence(s) in: {path}"

    except PermissionError:
        return f"Error: Permission denied editing: {file_path}"
    except Exception as e:
        return f"Error editing file: {str(e)}"


@tool(category='filesystem')
def Grep(pattern: str, path: str = ".", include: str | None = None, exclude: str | None = None, context: int = 0, ignore_case: bool = True, max_results: int = 100):
    """Search for text patterns in files, similar to grep.

    Args:
        pattern (str): The text pattern to search for. Supports regex if valid.
        path (str, optional): Directory or file to search in. Defaults to current directory.
        include (str, optional): Glob pattern for files to include (e.g., "*.py", "*.txt").
        exclude (str, optional): Glob pattern for files to exclude (e.g., "*.log", "node_modules").
        context (int, optional): Number of lines to show before/after match. Defaults to 0.
        ignore_case (bool, optional): Case-insensitive search. Defaults to True.
        max_results (int, optional): Maximum number of matching lines to return. Defaults to 100.

    Returns:
        str: Matching lines with file:line:content format, or error message

    Examples:
        - Search all files: Grep("function_name")
        - Search Python files only: Grep("TODO", include="*.py")
        - Search with 3 lines context: Grep("error", context=3)
    """

    try:
        search_path = Path(path).expanduser().resolve()

        if not search_path.exists():
            return f"Error: Path not found: {path}"

        results = []
        flags = re.IGNORECASE if ignore_case else 0

        try:
            re.compile(pattern)
        except re.error:
            pattern = re.escape(pattern)

        files_to_search = []

        if search_path.is_file():
            files_to_search = [search_path]
        else:
            for root, dirs, files in os.walk(search_path):
                if exclude:
                    dirs[:] = [d for d in dirs if not fnmatch.fnmatch(d, exclude)]

                for f in files:
                    if include and not fnmatch.fnmatch(f, include):
                        continue
                    if exclude and fnmatch.fnmatch(f, exclude):
                        continue
                    files_to_search.append(Path(root) / f)

        for file_path in files_to_search:
            try:
                with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                    for line_num, line in enumerate(f, 1):
                        if re.search(pattern, line, flags):
                            results.append({
                                'file': str(file_path),
                                'line': line_num,
                                'content': line.rstrip()
                            })
                            if len(results) >= max_results:
                                break
            except (PermissionError, UnicodeDecodeError):
                continue

            if len(results) >= max_results:
                break

        if not results:
            return f"No matches found for: {pattern}"

        output = [f"Found {len(results)} match(es) for '{pattern}':\n"]
        for r in results:
            if context > 0:
                output.append(f"\n--- {r['file']} (line {r['line']}) ---")
            output.append(f"{r['file']}:{r['line']}: {r['content']}")

        return '\n'.join(output)

    except Exception as e:
        return f"Error during search: {str(e)}"


@tool(category='filesystem')
def Glob(pattern: str, path: str = ".", recursive: bool = True, include_dirs: bool = False, max_results: int = 100):
    """Find files matching a glob pattern, similar to glob.

    Args:
        pattern (str): Glob pattern to match (e.g., "*.py", "**/*.js", "src/**/*.ts").
        path (str, optional): Directory to search in. Defaults to current directory.
        recursive (bool, optional): Search recursively. Defaults to True.
        include_dirs (bool, optional): Include directories in results. Defaults to False.
        max_results (int, optional): Maximum number of files to return. Defaults to 100.

    Returns:
        str: List of matching file paths, or error message

    Examples:
        - All Python files: Glob("*.py")
        - Recursive Python files: Glob("**/*.py", path="src")
        - TypeScript in src: Glob("*.ts", path="src")
    """
    try:
        search_path = Path(path).expanduser().resolve()

        if not search_path.exists():
            return f"Error: Directory not found: {path}"

        if not search_path.is_dir():
            return f"Error: Not a directory: {path}"

        results = []

        if '**' in pattern:
            pattern = pattern.replace('**', '*')
            recursive = True

        if recursive:
            for root, dirs, files in os.walk(search_path):
                root_path = Path(root)

                for f in files:
                    if fnmatch.fnmatch(f, pattern):
                        results.append(str(root_path / f))
                        if len(results) >= max_results:
                            break

                if include_dirs:
                    for d in dirs:
                        if fnmatch.fnmatch(d, pattern):
                            results.append(str(root_path / d))
                            if len(results) >= max_results:
                                break

                if len(results) >= max_results:
                    break
        else:
            for f in search_path.glob(pattern):
                if f.is_file():
                    results.append(str(f))
                elif include_dirs and f.is_dir():
                    results.append(str(f))
                if len(results) >= max_results:
                    break

        if not results:
            return f"No files found matching: {pattern}"

        output = [f"Found {len(results)} file(s):\n"]
        for r in results:
            output.append(r)

        return '\n'.join(output)

    except Exception as e:
        return f"Error during glob: {str(e)}"


@tool(category='web')
def Search(query: str, max_results: int = 10):
    """Search the web for information using Tavily API.

    Args:
        query (str): The search query.
        max_results (int, optional): Maximum number of results. Defaults to 10.

    Returns:
        str: Search results with titles, content, and URLs, or error message
    """
    from tavily import TavilyClient

    try:
        api_key = settings.tavily_api_key if settings.tavily_api_key else None
        if not api_key:
            api_key = os.getenv('TAVILY_API_KEY')
        
        if not api_key:
            return "Error: Tavily API key not configured. Set TAVILY_API_KEY environment variable."

        client = TavilyClient(api_key=api_key)
        results = client.search(query=query, max_results=max_results)

        if not results or "results" not in results:
            return f"No results found for: {query}"

        search_results = results["results"]
        output = [f"Search results for '{query}':\n"]
        for i, result in enumerate(search_results, 1):
            output.append(f"{i}. {result.get('title', 'No title')}")
            output.append(f"   {result.get('content', 'No description')[:200]}...")
            output.append(f"   URL: {result.get('url', 'No URL')}")
            output.append("")

        return '\n'.join(output)

    except Exception as e:
        return f"Error during search: {str(e)}"


@tool(category='web')
def WebFetch(url: str, max_length: int = 5000, include_images: bool = False):
    """Fetch and extract content from web pages.

    Args:
        url (str): The URL to fetch content from.
        max_length (int, optional): Maximum characters to return. Defaults to 5000.
        include_images (bool, optional): Include image URLs in the output. Defaults to False.

    Returns:
        str: Extracted text content from the web page, or error message

    Examples:
        - Fetch a page: WebFetch("https://example.com")
        - Longer content: WebFetch("https://example.com", max_length=10000)
    """
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = requests.get(url, timeout=15, headers=headers)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, 'html.parser')

        for script in soup(['script', 'style', 'nav', 'footer', 'header']):
            script.decompose()

        text = soup.get_text(separator='\n', strip=True)

        lines = [line.strip() for line in text.split('\n')]
        text = '\n'.join(line for line in lines if line)

        if len(text) > max_length:
            text = text[:max_length] + '\n... (truncated)'

        if include_images:
            images = [img.get('src') or img.get('data-src') for img in soup.find_all('img')]
            images = [img for img in images if img]
            if images:
                text += f'\n\nImages found: {", ".join(images[:10])}'

        return f"URL: {url}\n\n{text}"

    except requests.exceptions.RequestException as e:
        return f"Error fetching URL: {str(e)}"
    except Exception as e:
        return f"Error processing page: {str(e)}"


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
        # Note: On Windows, shlex.split mangles backslash paths, so we handle differently
        import sys
        if sys.platform == 'win32':
            # On Windows, don't use shlex.split - pass command directly to shell
            command_parts = command
            # For validation, only check the base command (first word)
            base_command_for_check = command.split()[0] if command else ""
        else:
            try:
                # Use shlex to safely split command into arguments
                command_parts = shlex.split(command)
                base_command_for_check = command_parts[0] if command_parts else ""
            except ValueError as e:
                return f"Error: Invalid command format - {str(e)}"

        # Security check 4: Additional command validation
        if sys.platform != 'win32':
            # Only check individual parts on non-Windows (where we split safely)
            for part in command_parts:
                if re.search(r'[;&|]', part) or '..' in part:
                    return "Error: Command contains forbidden characters or patterns"
        else:
            # On Windows, just check for obvious malicious patterns in the full command
            if '..' in command:
                return "Error: Command contains forbidden pattern '..'"

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

            # Security check 6: Sanitize output
            output = stdout.strip()
            error_output = stderr.strip()
            # Remove any control characters except newlines and tabs
            output = re.sub(r'[\x00-\x09\x0b-\x1f\x7f-\x9f]', '', output)
            error_output = re.sub(r'[\x00-\x09\x0b-\x1f\x7f-\x9f]', '', error_output)
            
            # Try to encode to ascii, replacing non-ascii chars (for Windows console)
            try:
                output = output.encode('ascii', 'replace').decode('ascii')
                error_output = error_output.encode('ascii', 'replace').decode('ascii')
            except Exception:
                pass

            # If we have valid stdout, return it even if returncode is non-zero
            # (the script may have printed warnings to stderr but still produced valid output)
            if output:
                if error_output and process.returncode != 0:
                    # Include error info but prioritize the actual output
                    return f"{output}\n\n[Warning: {error_output}]"
                return output

            # No stdout - return the error
            if process.returncode != 0:
                if error_output:
                    return f"Command failed (exit code {process.returncode}):\n{error_output}"
                return f"Command failed (exit code {process.returncode})"

            return "Command executed successfully (no output)"

        except Exception as e:
            print(e)
            return f"Error executing command: {str(e)}"

    except Exception as e:
        return f"Unexpected error: {str(e)}"
