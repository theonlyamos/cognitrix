import json
import shutil
from webbrowser import open_new_tab
from typing import Union, Optional, Any, Tuple, Dict, List
from webbrowser import open_new_tab
from ..tools.base import Tool
from ..tools.tool import tool
from cognitrix.agents import Agent
from cognitrix.llms import LLM
from cognitrix.tasks import Task
from pydantic import Field
from serpapi import google_search
from pathlib import Path
from PIL import Image
import pyautogui
import requests
import logging 
import aiohttp
import base64
import sys
import os
import io

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
    def run(self, topic: Optional[str] = None, category: Optional[str] = None, country: Optional[str] = None):
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
    documents_path: str = os.path.join(home_path, 'Documents').replace('\\', '\\\\')
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
    
    User: How many files are in my documents.
    AI Assistant: {{
        "type": "function_call",
        "function": "File System Browser",
        "arguments": ["{documents_path}", "list"]
    }}
    
    
    :param path: The specific path (realpath)
    :param operation: The operation to perform
    :param filename: (Optional) Name of file to create
    :param content: (Optional) Content to write to file
    """
    
    def run(self, path: str | Path, operation: str, filename: Optional[str] = None, content: Optional[str] = None):
        path = Path(path).resolve()
        operations = {
            'open': self.execute,
            'list': self.listdir,
            # 'create': self.create_path,
            'read': self.read_path,
            'create': self.write_file,
            'write': self.write_file,
            'delete': self.delete_path
        }
        
        if operation in ['write', 'create']:
            return operations[operation](path, filename, content)
        elif operation == 'open':
            return operations[operation](path, filename)
        return operations[operation](path)
    
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
        return 'The content of the directory are: '+json.dumps(os.listdir(path))
    
    def create_path(self, path: Path):
        if path.is_file():
            with path.open('wt') as file:
                return file
        return path.mkdir()
    
    def read_path(self, path: Path):
        if path.is_file():
            with path.open('rt') as file:
                return file.read()
        return os.listdir(path)
    
    def write_file(self, path: Path, filename: str, content: str):
        with path.joinpath(filename).open('wt') as file:
            return file.write(content)
    
    def delete_path(self, path: Path):
        if path.is_file():
            return path.unlink()
        return shutil.rmtree(path)
    
class SearchTool(Tool):
    """Wrapper around SerpAPI.

    To use, you should have the ``google-search-results`` python package installed,
    and the environment variable ``SERPAPI_API_KEY`` set with your API key, or pass
    `serpapi_api_key` as a named parameter to the constructor.

    Example:
        .. code-block:: python

            from langchain.utilities import SerpAPIWrapper
            serpapi = SerpAPIWrapper()
    """
    
    name: str = "Current Search"
    description: str = "Use this tool to search for current information"

    search_engine: Any = google_search #: :meta private:
    params: dict = Field(
        default={
            "engine": "google",
            "google_domain": "google.com",
            "gl": "us",
            "hl": "en",
        }
    )
    serpapi_api_key: Optional[str] = os.getenv('SERPAPI_API_KEY')
    aiosession: Optional[aiohttp.ClientSession] = None

    async def arun(self, query: str, **kwargs: Any) -> Union[str, List]:
        """Run query through SerpAPI and parse result async."""
        return self._process_response(await self.aresults(query))

    def run(self, query: str, **kwargs: Any) -> Union[str,List]:
        """Run query through SerpAPI and parse result."""
        return self._process_response(self.results(query))

    def results(self, query: str) -> dict:
        """Run query through SerpAPI and return the raw result."""
        params = self.get_params(query)
        search = self.search_engine(params)
        res = search.get_dict()
        return res

    async def aresults(self, query: str) -> dict:
        """Use aiohttp to run query through SerpAPI and return the results async."""

        def construct_url_and_params() -> Tuple[str, Dict[str, str]]:
            params = self.get_params(query)
            params["source"] = "python"
            if self.serpapi_api_key:
                params["serp_api_key"] = self.serpapi_api_key
            params["output"] = "json"
            url = "https://serpapi.com/search"
            return url, params

        url, params = construct_url_and_params()
        if not self.aiosession:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params) as response:
                    res = await response.json()
        else:
            async with self.aiosession.get(url, params=params) as response:
                res = await response.json()

        return res

    def get_params(self, query: str) -> Dict[str, str]:
        """Get parameters for SerpAPI."""
        _params = {
            "api_key": self.serpapi_api_key,
            "q": query,
        }
        params = {**self.params, **_params}
        return params

    @staticmethod
    def _process_response(res: dict) -> Union[str,List]:
        """Process response from SerpAPI."""
        
        toret: Union[str, List] = ""
        if "error" in res.keys():
            raise ValueError(f"Got error from SerpAPI: {res['error']}")
        if "answer_box" in res.keys() and type(res["answer_box"]) == list:
            res["answer_box"] = res["answer_box"][0]
        if "answer_box" in res.keys() and "answer" in res["answer_box"].keys():
            toret = res["answer_box"]["answer"]
        elif "answer_box" in res.keys() and "snippet" in res["answer_box"].keys():
            toret = res["answer_box"]["snippet"]
        elif (
            "answer_box" in res.keys()
            and "snippet_highlighted_words" in res["answer_box"].keys()
        ):
            toret = res["answer_box"]["snippet_highlighted_words"][0]
        elif (
            "sports_results" in res.keys()
            and "game_spotlight" in res["sports_results"].keys()
        ):
            toret = res["sports_results"]["game_spotlight"]
        elif (
            "shopping_results" in res.keys()
            and "title" in res["shopping_results"][0].keys()
        ):
            toret = res["shopping_results"][:3]
        elif (
            "knowledge_graph" in res.keys()
            and "description" in res["knowledge_graph"].keys()
        ):
            toret = res["knowledge_graph"]["description"]
        elif "snippet" in res["organic_results"][0].keys():
            toret = res["organic_results"][0]["snippet"]
        elif "link" in res["organic_results"][0].keys():
            toret = res["organic_results"][0]["link"]
        elif (
            "images_results" in res.keys()
            and "thumbnail" in res["images_results"][0].keys()
        ):
            thumbnails = [item["thumbnail"] for item in res["images_results"][:10]]
            toret = thumbnails
        else:
            toret = "No good search result found"
        return toret

@tool
def take_screenshot():
    """Use this tool to take a screenshot of the screen.
    Example:
    User: take a screenshot
    AI Assistant: {
        "type": "function_call",
        "function": "Take Screenshot",
        "arguments": []
    }"""
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
            "type": "function_call",
            "function": "Text Input",
            "arguments": ["hello world"]
        }
    """
    screenshot = pyautogui.write(text, 0.25)
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
            "type": "function_call",
            "function": "Key Press",
            "arguments": ["win"]
        }
    """
    screenshot = pyautogui.press(key.lower())
    
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
            "type": "function_call",
            "function": "Mouse Right Click",
            "arguments": ["123", "456"]
        }
    """
    screenshot = pyautogui.rightClick(x, y)
    
    return 'Mouse double-click completed.'

@tool
def create_agents(agent_details: list[list], parent_id):
    f"""Use this tool to create sub agents for specific tasks.
    Args:
        agent_details (list): Detailes needed to create an agent.
            Each list should contain three items (name, task, llm).
            name (str) - The name of the agent
            prompt (str) - Prompt describing the agent's role and functionalities
            task (str) - A brief description of the task for the agent (Can be empty. To be called later.)
            llm (str) - The name of the llm to use. Choose from {LLM.list_llms()}
            autostart (bool) - Whether the agent should immediately run it's task
    
    Returns:
        list[Agents]: A list of created sub agents
    
    Example:
        User: Create the snake game in python
        AI Assistant: {
            "type": "function_call",
            "function": "Create Agents",
            "arguments": [
                [
                    ["CodeWizard", "You are an experienced coder in the python language", "Write the code for the game in python", "openai", True],
                    ["TestWizard", "You are an experienced code tester", "Write and run tests for code written by CodeWizard", "", "claude", False]
                ]
            ]
        }
    """
    
    sub_agents = []
    
    for detail in agent_details:
        loaded_llm = LLM.load_llm(detail[3])
        if loaded_llm:
            agent_llm = loaded_llm()
            new_agent = Agent.create_agent(
                name=detail[0],
                task_description=detail[1],
                llm=agent_llm,
                is_sub_agent=True,
                parent_id=parent_id
            )
            if new_agent:
                new_agent.prompt_template = detail[2]
                sub_agents.append(new_agent)
                print('[+] Created sub agent', detail[0].title())
    
    return ['agents', sub_agents, 'Sub agents created successfully']

@tool
def call_sub_agent(name: str, task: str, parent: Agent):
    """Run a task with a sub agent
    
    Args:
        name (str): Name of the agent to call
    """
    parent.call_sub_agent(name, task)

# @tool
# def get_ui_elements_coordinates():
#     import cv2
#     import numpy as np
    
#     def get_ui_element_coords(image_path, element_name):
#         # Load the image
#         image = cv2.imread(image_path)
        
#         # Convert the image to grayscale
#         gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        
#         # Perform template matching to find the UI element
#         template = cv2.imread(f'{element_name}.png', 0)
#         res = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)
#         min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)
        
#         # Get the coordinates of the matched region
#         top_left = max_loc
#         bottom_right = (top_left[0] + template.shape[1], top_left[1] + template.shape[0])
        
#         # Return the coordinates
#         return top_left, bottom_right
    
#     # Example usage
#     image_path = 'screenshot.png'
#     element_name = 'brave'
#     coords = get_ui_element_coords(image_path, element_name)
#     print(f'The coordinates of the {element_name} icon are: {coords}')