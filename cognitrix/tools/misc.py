from __future__ import annotations

import contextvars
import fnmatch
import logging
import os
import re
from pathlib import Path
from typing import Any

from rich import print

from cognitrix.common.safe_exec import (
    DEFAULT_TIMEOUT,
    CommandNotAllowed,
    PathEscapesRoot,
    resolve_within_root,
    run_whitelisted,
)
from cognitrix.config import settings
from cognitrix.tools.tool import tool

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
    datefmt='%d-%b-%y %H:%M:%S',
    level=logging.WARNING
)
logger = logging.getLogger('cognitrix.log')


def _pyautogui():
    """Import pyautogui lazily. It (and its tkinter/cv2 deps) added ~0.15s+ to
    every startup and can crash on import in a headless server/worker, yet is
    only used by the screen-automation tools. Cached by sys.modules after first
    import."""
    import pyautogui
    return pyautogui

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


def _read_pdf(path: Path, page_range: str | None = None) -> str:
    """Extract text from a PDF file."""
    try:
        import fitz  # pymupdf
    except ImportError:
        return "Error: PyMuPDF is required to read PDF files.\nInstall with: pip install pymupdf"

    try:
        doc = fitz.open(str(path))
        total_pages = len(doc)

        # Parse page range
        pages_to_extract = None
        if page_range:
            pages_to_extract = _parse_page_range(page_range, total_pages)
        else:
            pages_to_extract = list(range(total_pages))

        result = {
            'file': path.name,
            'total_pages': total_pages,
            'extracted_pages': len(pages_to_extract),
            'pages': [],
        }

        for page_num in pages_to_extract:
            if page_num >= total_pages:
                continue
            page = doc[page_num]
            text = page.get_text('text')

            result['pages'].append({
                'number': page_num + 1,
                'text': text.strip(),
                'char_count': len(text.strip()),
            })

        doc.close()

        # Format output
        lines = []
        lines.append(f"## Document: {result['file']}")
        lines.append(f"**Pages:** {result['total_pages']} total, {result['extracted_pages']} extracted\n")

        for page in result['pages']:
            lines.append(f"### Page {page['number']}")
            if page['text']:
                text = page['text'].encode('ascii', 'ignore').decode('ascii')
                lines.append(text)
            else:
                lines.append("*(no text content — possibly a scanned/image page)*")
            lines.append("")

        return '\n'.join(lines)

    except Exception as e:
        return f"Error reading PDF: {str(e)}"


def _parse_page_range(range_str: str, total_pages: int) -> list[int]:
    """Parse a page range string into a list of 0-indexed page numbers."""
    pages = []

    if not range_str:
        return list(range(total_pages))

    if '-' in range_str:
        parts = range_str.split('-', 1)
        start = max(0, int(parts[0]) - 1)
        end = min(total_pages, int(parts[1]))
        pages = list(range(start, end))
    elif ',' in range_str:
        for p in range_str.split(','):
            p = p.strip()
            if p.isdigit():
                idx = int(p) - 1
                if 0 <= idx < total_pages:
                    pages.append(idx)
    elif range_str.isdigit():
        idx = int(range_str) - 1
        if 0 <= idx < total_pages:
            pages = [idx]
    else:
        pages = list(range(total_pages))

    return pages


@tool(category='filesystem')
def Read(file_path: str, start_line: int = 1, end_line: int | None = None, show_line_numbers: bool = True, page_range: str | None = None):
    """Read the contents of a file or PDF, optionally with range selection.

    Args:
        file_path (str): Path to the file to read. Supports absolute and relative paths.
        start_line (int, optional): Starting line number for text files (1-based). Defaults to 1.
        end_line (int, optional): Ending line number for text files (1-based). If None, reads to end.
        show_line_numbers (bool, optional): Whether to show line numbers for text files. Defaults to True.
        page_range (str, optional): For PDFs only. Page range like "1-5", "1,3,5", or "3". Defaults to all pages.

    Returns:
        str: File contents with line/page numbers, or error message

    Examples:
        - Read text file: Read("path/to/file.py")
        - Read first 100 lines: Read("file.py", end_line=100)
        - Read PDF all pages: Read("document.pdf")
        - Read PDF pages 1-5: Read("document.pdf", page_range="1-5")
    """
    try:
        try:
            path = resolve_within_root(file_path, settings.tools_root)
        except PathEscapesRoot as e:
            return f"Error: {e}"

        if not path.exists():
            return f"Error: File not found: {file_path}"

        if not path.is_file():
            return f"Error: Not a file: {file_path}"

        # Check if file is PDF
        if path.suffix.lower() == '.pdf':
            return _read_pdf(path, page_range)

        # Text file reading - existing logic
        with open(path, encoding='utf-8', errors='replace') as f:
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
        try:
            path = resolve_within_root(file_path, settings.tools_root)
        except PathEscapesRoot as e:
            return f"Error: {e}"

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
        try:
            path = resolve_within_root(file_path, settings.tools_root)
        except PathEscapesRoot as e:
            return f"Error: {e}"

        if not path.exists():
            if create_if_missing:
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(new_string)
                return f"Created file with content: {path}"
            return f"Error: File not found: {file_path}"

        if not old_string:
            return "Error: old_string cannot be empty"

        with open(path, encoding='utf-8', errors='replace') as f:
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
                with open(file_path, encoding='utf-8', errors='replace') as f:
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

    try:
        # Lazy import: tavily (and its transitive cohere dep) added ~0.7s+ to
        # every startup but is only needed when the Search tool actually runs.
        from tavily import TavilyClient

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
        import requests
        from bs4 import BeautifulSoup

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
    screenshot = _pyautogui().screenshot()

    return ['image', screenshot]

@tool(category='system')
def text_input(text: str):
    """Use this tool to take make text inputs.
    Args:
        text (str): Text to input.

    Returns:
        str: Text input completed.
    """
    _pyautogui().write(text, 0.15)

    return 'Text input completed'

@tool(category='system')
def key_press(key: str):
    """Use this tool to take make key presses.
    Args:
        key (str): Name of key to press.

    Returns:
        str: Keypress completed.
    """
    _pyautogui().press(key.lower())

    return 'Keypress completed'

@tool(category='system')
def hot_key(hotkeys: list):
    """Use this tool to take make hot key presses.
    Args:
        hotkeys (list): list of keys to press together.

    Returns:
        str: Keypress completed.
    """
    _pyautogui().hotkey(*hotkeys)

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
    _pyautogui().click(x, y)

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
    _pyautogui().doubleClick(x, y)

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
    _pyautogui().rightClick(x, y)

    return 'Mouse double-click completed.'

@tool(category='system')
async def create_agent(name: str, provider: str, description: str, tools: list[str], model: str | None = None, temperature: float | None = None, parent: Any | None = None):
    """Use this tool to create sub agents for specific tasks.

    Args:
        name (str): The name of the agent
        provider (str): The name of the provider to use. Select from [openai, google, anthropic, groq, together, clarifai].
        description (str): Prompt describing the agent's role and functionalities. Should include the agent's role, capabilities and any other info the agent needs to be able to complete it's task. Be as thorough as possible.
        tools (list): Tools the agent needs to complete it's tasks if any.
        model (str, optional): Model id to use. Omit to use the provider's default model.
        temperature (float, optional): Sampling temperature. Omit to use the provider's default temperature.

    Returns:
        str: A message indicating whether the the sub agent was created or not.
    """

    # Local import to avoid circular-import issues.
    from cognitrix.agents import Agent  # noqa: WPS433  (allow internal import)

    agent = await Agent.create_agent(  # type: ignore[attr-defined]
        name=name,
        system_prompt=description,
        provider=provider,
        model=model or '',
        temperature=temperature,
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
async def list_agents():
    """List the saved agents available to delegate tasks to (e.g. with call_agent).

    Returns:
        str: Each agent's name, provider/model, and a short description.
    """
    from cognitrix.agents import Agent  # noqa: WPS433

    agents = await Agent.list_agents()  # type: ignore[attr-defined]
    if not agents:
        return "No agents found."

    lines = [f"{len(agents)} agent(s):"]
    for a in agents:
        llm = getattr(a, 'llm', None)
        provider = getattr(llm, 'provider', '') or '?'
        model = getattr(llm, 'model', '') or '?'
        desc = ' '.join((a.system_prompt or '').split())
        if len(desc) > 160:
            desc = desc[:160] + '…'
        line = f"- {a.name} [{provider}/{model}]"
        if desc:
            line += f" — {desc}"
        lines.append(line)
    return '\n'.join(lines)

# Delegation depth for call_agent: bounds agent->agent recursion (A calls B
# calls A ...). Context-local so concurrent turns don't share a counter.
_CALL_AGENT_DEPTH = contextvars.ContextVar('call_agent_depth', default=0)
MAX_AGENT_CALL_DEPTH = 3


@tool(category='system')
async def call_agent(name: str, task: str, interface: str = 'task'):
    """Run a task with a sub agent

    Args:
        name (str): Name of the agent to call
        task (str): The task|query to perform|answer
        interface (str): Filled in by the runtime; leave at its default.

    Returns:
        str: The result of the task

    Raises:
        Exception: If the agent is not found or the task fails
    """
    depth = _CALL_AGENT_DEPTH.get()
    if depth >= MAX_AGENT_CALL_DEPTH:
        return f"Error calling agent: delegation depth limit ({MAX_AGENT_CALL_DEPTH}) reached"
    token = _CALL_AGENT_DEPTH.set(depth + 1)
    try:
        from cognitrix.agents import Agent  # noqa: WPS433
        from cognitrix.sessions.base import Session  # noqa: WPS433

        agent = await Agent.find_one({'name': name})
        if not agent:
            return f"Error calling agent: {name} not found"

        chunks: list[str] = []

        async def capture(payload=None, *args, **kwargs):
            content = payload.get('content', '') if isinstance(payload, dict) else (str(payload) if payload else '')
            if content:
                chunks.append(content)

        # Run the task through the sub-agent's own session loop so it gets
        # tools, safety checks, and history like any other turn. 'cli' maps to
        # 'task' because the cli branch prints instead of awaiting the capture
        # callback; web/ws pass through so risky tools are denied by policy.
        session_interface = 'task' if interface in ('cli', 'task') else interface
        session = await Session.get_by_agent_id(str(agent.id))
        await session(task, agent, session_interface, True, capture, {})
        return ''.join(chunks).strip() or f"Agent '{name}' returned no output."
    except Exception as e:
        return f"Error calling agent: {str(e)}"
    finally:
        _CALL_AGENT_DEPTH.reset(token)

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
        from cognitrix.teams.base import TeamManager  # noqa: WPS433

        team_manager = TeamManager()
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


# create_tool removed: it ran attacker-supplied source via exec() with no sandbox
# (unauthenticated RCE). Follows the ede84af precedent that removed python_repl/calculator.


@tool(category='system')
def bash(command: str, timeout: int | None = 180, working_dir: str | None = str(Path.cwd())) -> str:
    """Execute a single whitelisted terminal command.

    Only one command runs per call — command chaining and shell operators
    (`;`, `&&`, `|`, `>`, `$()`, backticks, `..`) are rejected, and only
    whitelisted base commands are allowed (ls, cat, grep, find, git, python,
    pip, node, npm, mkdir, mv, cp, touch, ...). To run a command inside a
    subdirectory (e.g. run tests for a package that lives in a subfolder), pass
    that folder as working_dir rather than using `cd` — e.g. bash("python -m
    pytest tests", working_dir="myproject"). `python -c`/`node -e` inline code is
    not allowed; put code in a file and run the file instead.

    Args:
        command (str): The single command to execute (no chaining).
        timeout (int, optional): Maximum execution time in seconds.
        working_dir (str, optional): Directory to run the command in. Use this
            instead of `cd` to operate in a subfolder. Defaults to current dir.

    Returns:
        str: Command output or error message.
    """
    # Validate and resolve the working directory
    if working_dir:
        try:
            work_dir = Path(working_dir).resolve()
            if not work_dir.exists() or not work_dir.is_dir():
                return f"Error: Invalid working directory - {working_dir}"
        except Exception as e:
            return f"Error: Working directory validation failed - {str(e)}"
    else:
        work_dir = Path.cwd()

    # Normalise timeout (the LLM may pass it as a string)
    try:
        timeout_s = int(timeout) if timeout is not None else DEFAULT_TIMEOUT
    except (ValueError, TypeError):
        return f"Error: Invalid timeout value '{timeout}'. Must be an integer."

    # Sandbox mode: run through a real shell (pipes, &&, arbitrary commands),
    # bypassing the whitelist. ONLY for throwaway sandboxes (benchmark/CI
    # containers) where the environment itself is the isolation boundary — never
    # enable on a host you care about. Off by default.
    if os.getenv('COGNITRIX_SANDBOX_SHELL', '').strip().lower() in ('1', 'true', 'yes'):
        return _run_sandbox_shell(command, cwd=str(work_dir), timeout=timeout_s)

    # Default: the shared safety boundary — whitelist + argv + shell=False.
    try:
        return run_whitelisted(command, cwd=str(work_dir), timeout=timeout_s)
    except CommandNotAllowed as e:
        return f"Error: {e}"
    except Exception as e:
        logger.exception("bash tool failed")
        return f"Error executing command: {e}"


def _run_sandbox_shell(command: str, cwd: str, timeout: int) -> str:
    """Run a command through a real shell (no whitelist). Sandbox-only; gated by
    COGNITRIX_SANDBOX_SHELL."""
    import subprocess
    try:
        proc = subprocess.run(
            command, shell=True, cwd=cwd, timeout=timeout,
            capture_output=True, text=True,
        )
    except subprocess.TimeoutExpired:
        return f"Command timed out after {timeout}s"
    out = (proc.stdout or '').strip()
    err = (proc.stderr or '').strip()
    if proc.returncode != 0:
        detail = err or out or f"exit code {proc.returncode}"
        return f"Command failed (exit {proc.returncode}): {detail}"
    return out or (f"[stderr: {err}]" if err else "Command executed successfully (no output)")
