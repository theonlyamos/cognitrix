import asyncio
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
from serpapi.google_search import GoogleSearch
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

class Calculator(Tool):
    name: str = 'Calculator'
    
    description: str =  """
        Useful for getting the result of a math expression.
        The input to this tool should be a valid mathematical expression that could be executed by a simple calculator.
        The code will be executed in a python environment so the input should be in a format it can be executed.
        Always present the answer from this tool to the user in a sentence.
        
        Example:
        User: what is the square root of 25?
        arguments: 25**(1/2)
        
        :param math_express: The math expression to evaluate
        """
    
    def run(self, math_expression):
        return eval(math_expression)
    
    async def arun(self, math_expression):
        return await eval(math_expression)

class YoutubePlayer(Tool):
    name: str =  "Youtube Player"
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
    
    :param url: The url to visit
    """
    def run(self, url: str):
        open_new_tab(url)
        
        return f"Opened {url} on the internet browser"
    
    def arun(self, url: str):
        raise NotImplementedError(NotImplementedErrorMessage)

class WorldNews(Tool):
    name: str =  "World News"
    categories: list = ["business","entertainment","general",
                  "health","science","sports","technology"]
    description: str =  f"""
    Use this tool to fetch current news headlines.
    Only titles of the news should be presented to
    the user.
    
    Allowed categories are: {categories}
    The parameters for the news should be intuited
    from the user's query.
    
    Always convert the country to its 2-letter ISO 3166-1 code
    if the country parameter is needed before being used.
    
    Never use 'world' as a country.
    
    The results of this tool should alwasy be returned
    to the user as bullet points.
    
    :param topic (optional): The topic to search for
    :param category (optional): Category selected from categories
    :param country (optional): Country to search news from
    """
    def run(self, topic: Optional[str] = None, category: Optional[str] = 'general', country: Optional[str] = 'world'):
        try:
            url = "https://newsapi.org/v2/top-headlines"
            params={
                "apiKey": os.getenv('NEWSAPI_API_KEY'),
                "language": "en",
                "sources": "bbc-news,the-verge,google-news",
                "pageSize": 5
            }
            
            if topic:
                params["q"] = topic
            
            if any([category, country]) and category != 'general' and country not in ('world',''):
                del params['sources']
            
                if category:
                    params["category"] = category
            
                if country:
                    params["country"] = country
            
            response = requests.get(
                url,
                params=params
            )
            
            results = response.json()
            articles = results['articles']
            headlines = [line['title'] for line in articles]
            
            return headlines
        except Exception as e:
            return f"Error: {str(e)}"
    
    def arun(self, url: str):
        raise NotImplementedError(NotImplementedErrorMessage)

class FSBrowser(Tool):
    name: str =  "File System Browser"
    home_path: str = os.path.expanduser('~')
    desktop_path: str = os.path.join(home_path, 'Desktop').replace('\\', '\\\\')
    documents_path: str = os.path.join(home_path, 'Documents').replace('\\', '\\\\') #type: ignore
    description: str =  f"""use this tool when you need to perform
    file system operations like listing of directories,
    opening a file, creating a file, updating a file,
    reading from a file or deleting a file.
    
    This tool is for file reads and file writes
    actions.
    
    {sys.platform} is the platform.
    {home_path} is the home path.
    
    The operation to perform should be in this 
    list:- ['open', 'list', 'create', 
    'read', 'write', 'delete', 'execute'].
    
    The path should always be converted to absolute
    path before inputting to tool.
    
    For all operations except 'execute',
    always append the filename to the specified 
    directory.
    
    Example:
    User: create test.py on Desktop.
    AI Assistant: {{
        "type": "function_call",
        "function": "File System Browser",
        "arguments": ["{desktop_path}", "create", "test.py", "gibberish"]
    }}
    
    User: write new content to test.py on Desktop.
    AI Assistant: {{
        "type": "function_call",
        "function": "File System Browser",
        "arguments": ["{desktop_path}", "write", "gibberish"]
    }}
    
    User: create folder called NewApp on Desktop.
    AI Assistant: {{
        "type": "function_call",
        "function": "File System Browser",
        "arguments": ["{desktop_path}", "create", "NewApp"]
    }}
    
    User: How many files are in my documents.
    AI Assistant: {{
        "type": "function_call",
        "function": "File System Browser",
        "arguments": ["{documents_path}", "list"]
    }}
    
    
    :param path: The specific path (realpath)
    :param operation: The operation to perform
    :param filename: (Optional) Name of file or folder to create
    :param content: (Optional) Content to write to file
    """
    
    def run(self, path: str | Path, operation: str, filename: Optional[str] = None, content: Optional[str] = None):
        try:
            path = Path(path).resolve()
            operations = {
                'open': self.execute,
                'list': self.listdir,
                'create': self.create_path,
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
        else: 
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
    
@tool
def take_screenshot():
    """Use this tool to take a screenshot of the screen.
    
    Example:
        User: take a screenshot
        AI Assistant: {
            "observation": "I need to take a screenshot of the user's screen.",
            "thought": "Step 1) To take a screenshot, I need to use the Take Screenshot tool.  Step 2) Calling the Take Screenshot tool.",
            "type": "function_call",
            "function": "Take Screenshot",
            "arguments": []
        }
    """
    screenshot = pyautogui.screenshot()
    
    return ['image', screenshot]

@tool
def text_input(text: str):
    """Use this tool to take make text inputs.
    Args:
        text (str): Text to input.
    
    Returns:
        str: Text input completed.
    
    Example:
        User: write hello world
        AI Assistant: {
            "observation": "I need to input text on the computer using the keyboard.",
            "thought": "Step 1) To perform this action, I will need to use the Text Input tool. Step 2) The Text Input function takes one argument: text (a string representing the text to input). Step 2) Calling the Text Input function with argument: 'hello world'.",
            "type": "function_call",
            "function": "Text Input",
            "arguments": ["hello world"]
        }
    """
    screenshot = pyautogui.write(text, 0.15)
    
    return 'Text input completed'

@tool
def key_press(key: str):
    """Use this tool to take make key presses.
    Args:
        key (str): Name of key to press.
    
    Returns:
        str: Keypress completed.
    
    Example:
        User: Press windows key
        AI Assistant: {
            "observation": "I need to perform a single key press on the computer.",
            "thought": "Step 1) This action requires the usage of the Key Press tool. Step 2) The Key Press tool takes one argument: key (a string representing the name of the key to press). Step 3) Calling the Key Press tool with argument: 'win'.",
            "type": "function_call",
            "function": "Key Press",
            "arguments": ["win"]
        }
    """
    screenshot = pyautogui.press(key.lower())
    
    return 'Keypress completed'

@tool
def hot_key(*hotkeys):
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
            "type": "function_call",
            "function": "Hot Key",
            "arguments": ["ctrl", "v"]
        }
    """
    screenshot = pyautogui.hotkey(*hotkeys)
    
    return 'Keypress completed'

@tool
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
            "type": "function_call",
            "function": "Mouse Click",
            "arguments": ["123", "456"]
        }
    """
    screenshot = pyautogui.click(x, y)
    
    return 'Mouse Click completed'

@tool
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
            "type": "function_call",
            "function": "Mouse Double Click",
            "arguments": ["123", "456"]
        }
    """
    screenshot = pyautogui.doubleClick(x, y)
    
    return 'Mouse double-click completed.'

@tool
def mouse_right_click(x: int, y: int):
    """Use this tool to take make mouse right clicks.
    Args:
        x (int): X coordinate of mouse click.
        y (int): y coordinate of mouse click.
    
    Returns:
        str: Mouse right-click completed.
    
    Example:
        User: Right-click on Brave icon
        AI Assistant: {
            "observation": "I need to perform a mouse right-click at specific coordinates on the screen.",
            "thought": "Step 1) To perform the mouse right-click, I need to use the Mouse Right Click tool. Step 2) The Mouse Click tool takes two arguments: x (the x-coordinate of the mouse click) and y (the y-coordinate of the mouse click). Step 3) Calling the Mouse Right Click tool with the x,y coordinates",
            "type": "function_call",
            "function": "Mouse Right Click",
            "arguments": ["123", "456"]
        }
    """
    screenshot = pyautogui.rightClick(x, y)
    
    return 'Mouse double-click completed.'

@tool
async def create_sub_agent(name: str, llm: str, description: str, tools: List[str], parent: Agent):
    """Use this tool to create sub agents for specific tasks.
    
    Args:
        name (str) - The name of the agent
        llm (str) - The name of the llm to use. Select from [openai, google, anthropic, groq, together, clarifai].
        description (str) - Prompt describing the agent's role and functionalities. Should include the agent's role, capabilities and any other info the agent needs to be able to complete it's task. Be as thorough as possible.
        tools (list) - Tools the agent needs to complete it's tasks if any.
    
    Returns:
        str: A message indicating whether the the sub agent was created or not.
    
    Example:
        User: Create sub agent for a task
        AI Assistant: {
            "observation": "The user has requested me to create a sub-agent for a specific task.",
            "thought": "Step 1) To create the sub agent, I need to use the Create Sub Agent Tool. Step 2) The Create Sub Agent tool takes five arguments: name (the name of the sub-agent), description (a prompt describing the agent's role and capabilities), task (an optional brief description of the task), llm (the name of the language model to use), and autostart (a boolean indicating whether the agent should immediately run its task). Step 3) Calling the Create Sub Agent Tool with required arguments.",
            "type": "function_call",
            "function": "Create Sub Agent",
            "arguments": ["<agent_name>", "<llm>", "<agent_prompt>", []]
        }
        
        User: Create the snake game in python
        AI Assistant: {
            "observation": "The user has requested me to create the classic Snake game in Python.",
            "thought": "Step 1) To create the Snake game in Python, I will need to create a specialized sub-agent called 'CodeWizard' with expertise in Python programming. Step 2) I will provide the CodeWizard agent with the instructions to write the code for the Snake game in Python, following the specified return format. Step 3) I will delegate the task of writing the Python code for the Snake game to the CodeWizard agent.",
            "type": "function_call",
            "function": "Create Sub Agent",
            "arguments": ["CodeWizard", "gemini", "You are a skilled Python programmer tasked with creating the classic Snake game. Your role is to write clean, efficient, and well-documented code that implements the game's logic, user interface, and any additional features you deem necessary. You should follow best practices for software development and ensure your code is modular, readable, and maintainable.", ["File System Browser", "Call Sub Agent"]]
        }
    """
    
    sub_agent: Optional[Agent]
    
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

@tool
def call_sub_agent(name: str, task: str, parent: Agent):
    """Run a task with a sub agent
    
    Args:
        name (str): Name of the agent to call
        task (str): The task|query to perform|answer
    
    Example:
        User: Run task with sub agent
        
        AI Assistant: {
            "observation": "I need to run a task with a sub-agent.",
            "thought": "Step 1) This can be accomplished by using the Call Sub Agent Tool. Step 2) The Call Sub Agent tool takes two arguments: name (the name of the sub-agent to call) and task (the task or query for the sub-agent to perform or answer). Step 3) Calling the Call Sub Agent tool with required arguments",
            "type": "function_call",
            "function": "Call Sub Agent",
            "arguments": ["CodeWizard", "Write the code for the Snake game in Python."]
        }
    """
    parent.call_sub_agent(name, task)
    
    return "Agent running"

@tool
def internet_search(query: str):
    """Use this tool to search the internet.
    
    Args:
        query (str): The query to search for.
    
    Example:
        User: who is the president of the United States?
        AI Assistant: {
            "observation": "I need to search the internet for information about the president of the United States.",
            "thought": "Step 1) To do an internet search, I need to use the Internet Search tool. Step 2) The Internet Search tool takes one argument: query (the topic to search for).  Step 3) Calling the Take Screenshot tool with the provided arguments.",
            "type": "function_call",
            "function": "Internet Search",
            "arguments": ["who is the president of the United States?"]
        }
    """
    
    res = GoogleSearch({"q": query, "serp_api_key": os.getenv('SERPA_API_KEY', '')}).get_dict()

    toret: Union[str, List] = []
    if "error" in res.keys():
        raise ValueError(f"Got error from SerpAPI: {res['error']}")

    if "answer_box" in res.keys():
        toret.append({'answer_box': res['answer_box']})

    if "sports_results" in res.keys():
        toret.append({'sports_results': res['sports_results']})
    
    if "shopping_results" in res.keys():
        toret.append({'shopping_results': res['shopping_results']})
    
    if "knowledge_graph" in res.keys():
        toret.append({'knowledge_graph': res['knowledge_graph']})
    
    if "organic_results" in res.keys():
        toret.append({'organic_results': res['organic_results']})
    
    if "images_results" in res.keys():
        thumbnails = [item["thumbnail"] for item in res["images_results"][:10]]
        toret.append({'images_results': thumbnails})
    
    return json.dumps(toret)

@tool
def web_scraper(url: str):
    """Use this tool to scrape websites when given a link url.

    Args:
        url (str): The URL of the website to scrape.

    Returns:
        str: The text content of the scraped website.

    Example:
        User: Scrape the website https://example.com
        AI Assistant: {
            "observation": "The user has requested to scrape a website.",
            "thought": "Step 1) To scrape a website, I need to use the Web Scraper tool. Step 2) The Web Scraper tool takes one argument: url (the URL of the website to scrape). Step 3) Calling the Web Scraper tool with the provided URL.",
            "type": "function_call",
            "function": "Web Scraper",
            "arguments": ["https://example.com"]
        }
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