import json
import shutil
from webbrowser import open_new_tab
from typing import Union, Optional, Any, Tuple, Dict, List
from webbrowser import open_new_tab
from cognitrix.tools.base import Tool
from cognitrix.tools.tool import tool
from cognitrix.agents import Agent
from cognitrix.llms import LLM
from cognitrix.utils import json_return_format
from tavily import TavilyClient
from bs4 import BeautifulSoup
from pydantic import Field
from pathlib import Path
from PIL import Image
import pyautogui
import requests
import logging 
import aiohttp
import sys
import os

NotImplementedErrorMessage = 'this tool does not suport async'

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
    datefmt='%d-%b-%y %H:%M:%S',
    level=logging.WARNING
)
logger = logging.getLogger('cognitrix.log')

@tool(category='general')
def Calculator(math_expression: str):
    """
    Useful for getting the result of a math expression.
    The input to this tool should be a valid mathematical expression that could be executed by a simple calculator.
    The code will be executed in a python environment so the input should be in a format it can be executed.
    Always present the answer from this tool to the user in a sentence.

    Example:
        User: what is the square root of 25?
        Assistant: <response>
            <observation>The user is asking for the square root of 25 and has specified this should use the Calculator tool.</observation>
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
            <type>tool_calls</type>
            <tool_calls>
                <tool>
                    <name>Calculator</name>
                    <arguments>
                        <math_expression>sqrt(25)</math_expression>
                    </arguments>
                </tool>
            </tool_calls>
            <artifacts></artifacts>
        </response>

    :param math_expression: The math expression to evaluate
    """

    result = eval(math_expression)

    return result

class YoutubePlayer(Tool):
    name: str =  "Youtube Player"
    category: str = "web"
    description: str =  """
    use this tool when you need to play a youtube video
    
    :param topic (optional): The topic to search for
    """
    
    def run(self, topic: str):
        """Play a YouTube Video"""

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

        open_new_tab(f"https://www.youtube.com{lst[count - 5]}")
        return f"https://www.youtube.com{lst[count - 5]}"
    
    async def arun(self, url: str):
        raise NotImplementedError(NotImplementedErrorMessage)

class InternetBrowser(Tool):
    name: str =  "Internet Browser"
    description: str =  """
    use this tool when you need to visit a website
    
    Example:
    User: Visit https://example.com
    AI Assistant: <response>
        <observation>The user or system has requested to open a web page at https://example.com using the Internet Browser tool.</observation>
        <mindspace>
    Web Browsing: URL structure, web protocols (HTTP/HTTPS)
    Internet Security: Website trustworthiness, potential risks
    User Interface: Browser interaction, web page rendering
    Network: Internet connectivity, domain name resolution
        </mindspace>
        <thought>Step 1) The request is to open a specific web page, so we need to use the Internet Browser tool.
    Step 2) The URL provided is "https://example.com", which is a secure (HTTPS) website.
    Step 3) This appears to be a generic example URL, often used for testing or demonstration purposes.
    Step 4) Opening a web page is a common operation but may have implications for user privacy and security.</thought>
        <type>tool_calls</type>
        <tool_calls>
            <tool>
                <name>Internet Browser</name>
                <arguments>
                    <url>https://example.com</url>
                </arguments>
            </tool>
        </tool_calls>
        <artifacts></artifacts>
    </response>
    
    :param url: The url to visit
    """
    category: str = "web"
    
    def run(self, url: str):
        print(f"Opening {url} on the internet browser")
        
        open_new_tab(url)
        
        return f"Opened {url} on the internet browser"
    
    def arun(self, url: str):
        raise NotImplementedError(NotImplementedErrorMessage)

# class WorldNews(Tool):
#     name: str =  "World News"
#     categories: list = ["business","entertainment","general",
#                   "health","science","sports","technology"]
#     description: str =  f"""
#     Use this tool to fetch current news headlines.
#     Only titles of the news should be presented to
#     the user.
    
#     Allowed categories are: {categories}
#     The parameters for the news should be intuited
#     from the user's query.
    
#     Always convert the country to its 2-letter ISO 3166-1 code
#     if the country parameter is needed before being used.
    
#     Never use 'world' as a country.
    
#     The results of this tool should alwasy be returned
#     to the user as bullet points.
    
#     :param topic: The topic to search for
#     :param category: Category selected from categories {categories}
#     :param country (optional): Country to search news from
    
#     Example:
#     User: Get me the latest news on technology in the united states
#     AI Assistant: {{
#         "type": "tool_calls",
#         "tool_calls": [
#           {
#                "name": "World News",
#                "arguments": {
#                   "topic": "latest news on technology", 
#                   "category": "technology", 
#                   "country": "US"},
#               }
#         ]
#     }}
    
#     User: Get me news headlines from around the world
#     AI Assistant: {{
#         "type": "tool_calls",
#         "tool_calls": [
#           {
#                "name": "World News",
#                "arguments": {
#                   "topic": "news headlines around the world", 
#                   "category": "general"
#               }
#         ]
#     }}
#     """
#     def run(self, topic: Optional[str] = None, category: Optional[str] = 'general', country: Optional[str] = 'world'):
#         try:
#             url = "https://newsapi.org/v2/top-headlines"
#             params={
#                 "apiKey": os.getenv('NEWSAPI_API_KEY'),
#                 "language": "en",
#                 "sources": "bbc-news,the-verge,google-news",
#                 "pageSize": 5
#             }
            
#             if topic:
#                 params["q"] = topic
            
#             if any([category, country]) and category != 'general' and country not in ('world',''):
#                 del params['sources']
            
#                 if category:
#                     params["category"] = category
            
#                 if country:
#                     params["country"] = country
            
#             response = requests.get(
#                 url,
#                 params=params
#             )
            
#             results = response.json()
#             articles = results['articles']
#             headlines = [line['title'] for line in articles]
            
#             return json.dumps(headlines)
#         except Exception as e:
#             return f"Error: {str(e)}"
    
#     def arun(self, url: str):
#         raise NotImplementedError(NotImplementedErrorMessage)

class FSBrowser(Tool):
    name: str =  "File System Browser"
    category: str = "system"
    home_path: str = os.path.expanduser('~')
    desktop_path: str = os.path.join(home_path, 'Desktop').replace('\\', '\\\\')
    documents_path: str = os.path.join(home_path, 'Documents').replace('\\', '\\\\') #type: ignore
    description: str =  """use this tool when you need to perform
    file system operations like listing of directories,
    opening a file, creating a file, updating a file,
    reading from a file or deleting a file.
    
    This tool is for file reads and file writes
    actions.
    
    platform is the platform.
    home_path is the home path.
    
    The operation to perform should be in this 
    list:- ['open', 'list', 'create', "mkdir", 
    'read', 'write', 'delete', 'execute'].
    
    The path should always be converted to absolute
    path before inputting to tool.
    
    For all operations except 'execute',
    always append the filename to the specified 
    directory.
    
    Example:
    User: create test.py on Desktop.
    AI Assistant: <response>
        <observation>The user has requested to create a new file named 'test.py' on the desktop with content 'gibberish'.</observation>
        <mindspace>
    File System: File creation, directory structure, file permissions
    Programming: Python files, code editing, file naming conventions
    User Interface: Desktop environment, file management
    Security: File access, potential risks of creating files
        </mindspace>
        <thought>Step 1) The user wants to create a new file, so we need to use the File System Browser tool.
    Step 2) The file should be created on the desktop, which is specified by the 'desktop_path' argument.
    Step 3) The filename is set to 'test.py', indicating it's likely a Python script.
    Step 4) The content of the file is set to 'gibberish', which may not be valid Python code.</thought>
        <type>tool_calls</type>
        <tool_calls>
            <tool>
                <name>File System Browser</name>
                <arguments>
                    <path>{desktop_path}</path>
                    <operation>create</operation>
                    <filename>test.py</filename>
                    <content>gibberish</content>
                </arguments>
            </tool>
        </tool_calls>
        <artifacts></artifacts>
    </response>
    
    User: write new content to test.py on Desktop.
    AI Assistant: <response>
        <observation>The user has requested to write 'gibberish' content to a file named 'test.py' on the desktop.</observation>
        <mindspace>
    File System: File writing operations, file permissions
    Programming: Python files, code editing, file content management
    User Interface: Desktop environment, file access and modification
    Security: File overwriting, data integrity, potential risks
        </mindspace>
        <thought>Step 1) The user wants to write to a file, so we need to use the File System Browser tool.
    Step 2) The file is located on the desktop, specified by the 'desktop_path' argument.
    Step 3) The operation is 'write', which means we'll be modifying the content of an existing file or creating it if it doesn't exist.
    Step 4) The target file is 'test.py', which is likely a Python script.
    Step 5) The content to be written is 'gibberish', which may not be valid Python code and could potentially overwrite existing content.</thought>
        <type>tool_calls</type>
        <tool_calls>
            <tool>
                <name>File System Browser</name>
                <arguments>
                    <path>{desktop_path}</path>
                    <operation>write</operation>
                    <filename>test.py</filename>
                    <content>gibberish</content>
                </arguments>
            </tool>
        </tool_calls>
        <artifacts></artifacts>
    </response>
    
    User: create folder called NewApp on Desktop.
    AI Assistant: <response>
        <observation>The user has requested to create a new folder called 'NewApp' on the Desktop.</observation>
        <mindspace>
    File System: Directory creation, folder structure, permissions
    User Interface: Desktop organization, file management
    Project Management: Application development, workspace setup
    Naming Conventions: Folder naming best practices
        </mindspace>
        <thought>Step 1) The user wants to create a new folder, so we need to use the File System Browser tool.
    Step 2) The folder should be created on the desktop, which is specified by the 'desktop_path' argument.
    Step 3) The operation is 'mkdir', which stands for 'make directory', indicating we're creating a new folder.
    Step 4) The name of the new folder is set to 'NewApp', suggesting it might be used for a new application or project.
    Step 5) Creating a folder is generally a safe operation, but we should be aware of potential naming conflicts.</thought>
        <type>tool_calls</type>
        <tool_calls>
            <tool>
                <name>File System Browser</name>
                <arguments>
                    <path>{desktop_path}</path>
                    <operation>mkdir</operation>
                    <filename>NewApp</filename>
                </arguments>
            </tool>
        </tool_calls>
        <artifacts></artifacts>
    </response>
    
    User: How many files are in my documents.
    AI Assistant: <response>
        <observation>The user has asked to count the number of files in their documents folder.</observation>
        <mindspace>
    File System: Directory structure, file counting
    User Data: Personal document management, file organization
    System Information: File system queries, directory contents
    Privacy: Access to user's personal files, data sensitivity
        </mindspace>
        <thought>Step 1) To count the files in the documents folder, we first need to list its contents.
    Step 2) We'll use the File System Browser tool to accomplish this task.
    Step 3) The path should be set to 'documents_path' to target the user's documents folder.
    Step 4) The operation is 'list', which will retrieve the contents of the specified directory.
    Step 5) After getting the list, we'll need to count the number of files (excluding subdirectories).</thought>
        <type>tool_calls</type>
        <tool_calls>
            <tool>
                <name>File System Browser</name>
                <arguments>
                    <path>{documents_path}</path>
                    <operation>list</operation>
                </arguments>
            </tool>
        </tool_calls>
        <artifacts></artifacts>
    </response>
    
    
    :param path: The specific path (realpath)
    :param operation: The operation to perform
    :param filename: (Optional) Name of file or folder to create
    :param content: (Optional) Content to write to file
    """.replace("platform", sys.platform).replace('home_path', home_path).replace('desktop_path', desktop_path).replace('documents_path', documents_path)
    
    def run(self, path: str | Path, operation: str, filename: Optional[str] = None, content: Optional[str] = None):
        try:
            path = Path(path).resolve()
            operations = {
                'open': self.execute,
                'list': self.listdir,
                'create': self.create_path,
                'mkdir': self.mkdir,
                'read': self.read_path,
                # 'create': self.write_file,
                'write': self.write_file,
                'delete': self.delete_path
            }
            
            if operation in ['write', 'create']:
                return operations[operation](path, filename, content)
            elif operation in ['open', 'read']:
                return operations[operation](path, filename)
            return operations[operation](path)
        except Exception as e:
            return str(e)
    
    def arun(self, url: str):
        raise NotImplementedError(NotImplementedErrorMessage)
    
    def execute(self, path: Path, filename: Optional[str])->str:
        if filename and path.joinpath(filename).exists():
            os.startfile(path.joinpath(filename))
            return 'Successfully opened file'
        elif os.path.exists(path):
            os.startfile(path)
            return 'Successfully opened folder'
        return 'Unable to open file'
        
    def listdir(self, path: Path):
        if path.is_dir():
            return 'The content of the directory are: '+json.dumps(os.listdir(path))
        return "The path you provided isn't a directory"
    
    def create_path(self, path: Path, filename: Optional[str], content: Optional[str]):
        full_path = path.joinpath(filename) if filename else path
        if full_path.is_file():
            with full_path.open('wt') as file:
                if content:
                    file.write(content)
        
        return 'Operation done'
    
    def mkdir(self, path: Path, filename: Optional[str]):
        full_path = path.joinpath(filename) if filename else path
        if not full_path.is_dir() and full_path.exists():
            full_path.mkdir()
        
        return 'Operation done'
    
    def read_path(self, path: Path, filename: Optional[str] = None):
        full_path = path.joinpath(filename) if filename else path
        if full_path.is_file():
            with full_path.open('rt') as file:
                return file.read()
        elif full_path.is_dir(): 
            return json.dumps(os.listdir(full_path))
        else:
            return "Path wasn't found"
    
    def write_file(self, path: Path, filename: str, content: str):
        with path.joinpath(filename).open('wt') as file:
            file.write(content)
        
        return 'Write operation successful.'
    
    def delete_path(self, path: Path):
        if path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
        
        return 'Delete operation successfull'
    
@tool(category='system')
def take_screenshot():
    """Use this tool to take a screenshot of the screen.
    
    Usage Example:
    
    User: take a screenshot
    AI Assistant: <response>
        <observation>I need to take a screenshot of the user's screen.</observation>
        <mindspace>
    Screen Capture: Screenshot techniques, image formats
    User Interface: Current screen contents, visual information
    Privacy: Potentially sensitive information on screen
    System Interaction: Screen capture permissions, system API usage
        </mindspace>
        <thought>Step 1) To take a screenshot, I need to use the Take Screenshot tool.
    Step 2) Calling the Take Screenshot tool.</thought>
        <type>tool_calls</type>
        <tool_calls>
            <tool>
                <name>Take Screenshot</name>
                <arguments></arguments>
            </tool>
        </tool_calls>
        <artifacts></artifacts>
    </response>
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
    AI Assistant: <response>
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
        <type>tool_calls</type>
        <tool_calls>
            <tool>
                <name>Text Input</name>
                <arguments>
                    <text>hello world</text>
                </arguments>
            </tool>
        </tool_calls>
        <artifacts></artifacts>
    </response>
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
    AI Assistant: {
        "observation": "I need to perform a single key press on the computer.",
        "thought": "Step 1) This action requires the usage of the Key Press tool. Step 2) The Key Press tool takes one argument: key (a string representing the name of the key to press). Step 3) Calling the Key Press tool with argument: 'win'.",
        "type": "tool_calls",
        "tool_calls": [{}]: "Key Press",
        "arguments": ["win"]
    }
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
        AI Assistant: {
            "observation": "I need to perform a paste action using hotkey combination on the computer.",
            "thought": "Step 1) I need to use the Hot Key tool to perform this action. Step 2) The Hot Key tool takes a variable number of arguments (*hotkeys), which should be a list of keys to press together as a hotkey combination. Step 3) Calling the Hot Key tool with list of keys to press together.",
            "type": "tool_calls",
            "tool_calls": [{}]: "Hot Key",
            "arguments": ["ctrl", "v"]
        }
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
        AI Assistant: {
            "observation": "I need to perform a mouse click at specific coordinates on the screen.",
            "thought": "Step 1) To perform the mouse-click, I need to use the Mouse Click tool. Step 2) The Mouse Click tool takes two arguments: x (the x-coordinate of the mouse click) and y (the y-coordinate of the mouse click). Step 3) Calling the Mouse Click tool with the x,y coordinates",
            "type": "tool_calls",
            "tool_calls": [{}]: "Mouse Click",
            "arguments": ["123", "456"]
        }
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
        AI Assistant: {
            "observation": "I need to perform a mouse double-click at specific coordinates on the screen.",
            "thought": "Step 1) To perform the mouse double-click, I need to use the Mouse Double Click tool. Step 2) The Mouse Double Click tool takes two arguments: x (the x-coordinate of the mouse click) and y (the y-coordinate of the mouse click). Step 3) Calling the Mouse Double Click tool with the x,y coordinates",
            "type": "tool_calls",
            "tool_calls": [{}]: "Mouse Double Click",
            "arguments": ["123", "456"]
        }
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
        AI Assistant: <response>
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
            <type>tool_calls</type>
            <tool_calls>
                <tool>
                    <name>Mouse Right Click</name>
                    <arguments>
                        <x>123</x>
                        <y>456</y>
                    </arguments>
                </tool>
            </tool_calls>
            <artifacts></artifacts>
        </response>
    """
    screenshot = pyautogui.rightClick(x, y)
    
    return 'Mouse double-click completed.'

@tool(category='system')
async def create_sub_agent(name: str, llm: str, description: str, tools: List[str], parent: Agent):
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
        AI Assistant: <response>
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
            <type>tool_calls</type>
            <tool_calls>
                <tool>
                    <name>Create Sub Agent</name>
                    <arguments>
                        <name>&lt;agent_name&gt;</name>
                        <llm>&lt;llm&gt;</llm>
                        <description>&lt;agent_prompt&gt;</description>
                        <tools></tools>
                    </arguments>
                </tool>
            </tool_calls>
            <artifacts></artifacts>
        </response>
        
        User: Create the snake game in python
        AI Assistant: <response>
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
            <type>tool_calls</type>
            <tool_calls>
                <tool>
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
                </tool>
            </tool_calls>
            <artifacts></artifacts>
        </response>
    """
    
    sub_agent: Optional[Agent] = None
    
    loaded_tools: List[Tool] = []
    for t in tools:
        tool = Tool.get_by_name(t)
        if tool:
            loaded_tools.append(tool)

    loaded_llm = LLM.load_llm(llm)
    if loaded_llm:
        agent_llm = loaded_llm()
        
        description += '\n\n{tools}'
        
        if not "return_format" in description:
            description += f"\n{json_return_format}"
            
        sub_agent = await Agent.create_agent(
            name=name,
            description=description,
            llm=agent_llm,
            is_sub_agent=True,
            parent_id=parent.id,
            tools=loaded_tools
        )
        
    if sub_agent:
        sub_agent.prompt_template = description
        await sub_agent.save()
        return ['agent', sub_agent, 'Sub agent created successfully']
    
    return "Error creating sub agent"

@tool(category='system')
def call_sub_agent(name: str, task: str, parent: Agent):
    """Run a task with a sub agent
    
    Args:
        name (str): Name of the agent to call
        task (str): The task|query to perform|answer
    
    Example:
        User: Run task with sub agent
        
        AI Assistant: <response>
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
            <type>tool_calls</type>
            <tool_calls>
                <tool>
                    <name>Call Sub Agent</name>
                    <arguments>
                        <name>CodeWizard</name>
                        <task>Write the code for the Snake game in Python.</task>
                    </arguments>
                </tool>
            </tool_calls>
            <artifacts></artifacts>
        </response>
    """
    parent.call_sub_agent(name, task)
    
    return "Agent running"

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
        AI Assistant: <response>
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
            <type>tool_calls</type>
            <tool_calls>
                <tool>
                    <name>Internet Search</name>
                    <arguments>
                        <query>who is the president of the United States?</query>
                    </arguments>
                </tool>
            </tool_calls>
            <artifacts></artifacts>
        </response>
    """
    
    tavily = TavilyClient(api_key=os.getenv('TAVILY_API_KEY', ''))
    
    max_tokens = 500 if search_depth == "basic" else 1000
    
    response = tavily.get_search_context(query, search_depth, max_tokens)
    
    return response

@tool(category='web')
def web_scraper(url: str):
    """Use this tool to scrape websites when given a link url.

    Args:
        url (str): The URL of the website to scrape.

    Returns:
        str: The text content of the scraped website.

    Example:
        User: Scrape the website https://example.com
        AI Assistant: <response>
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
            <type>tool_calls</type>
            <tool_calls>
                <tool>
                    <name>Web Scraper</name>
                    <arguments>
                        <url>https://example.com</url>
                    </arguments>
                </tool>
            </tool_calls>
            <artifacts></artifacts>
        </response>
    """
    
    try:
        response = requests.get(url)
        response.raise_for_status()  # Raise an exception for non-2xx status codes
        html_content = response.text

        soup = BeautifulSoup(html_content, 'html.parser')
        text_content = soup.get_text()
        text_content = ' '.join(text_content.split())  # Remove empty spaces

        return text_content
    except requests.exceptions.RequestException as e:
        return f"Error: {e}"
    
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
        AI Assistant: <response>
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
            <type>tool_calls</type>
            <tool_calls>
                <tool>
                    <name>Brave Search</name>
                    <arguments>
                        <query>who is the president of the United States?</query>
                    </arguments>
                </tool>
            </tool_calls>
            <artifacts></artifacts>
        </response>
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
        # print(results)
        return results
    else:
        return f"Error: {response.status_code}, {response.text}"