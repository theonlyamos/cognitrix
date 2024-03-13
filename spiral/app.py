from llms import Together
from llms import Cohere
from agents import AIAssistant
from tools import (
    Calculator, YoutubePlayer,
    WorldNews, PythonREPL,
    FSBrowser, SearchTool,
    InternetBrowser, tool
)

@tool
def print_hello():
    """Returns hello world"""
    return 'hello world'

if __name__ == "__main__":
    llm = Cohere()
    # llm = TogetherLLM()
    assistant = AIAssistant(llm=llm, name='Adam')
    assistant.add_tool(print_hello())
    assistant.add_tool(Calculator())
    assistant.add_tool(YoutubePlayer())
    assistant.add_tool(WorldNews())
    assistant.add_tool(FSBrowser())
    assistant.add_tool(PythonREPL())
    assistant.add_tool(InternetBrowser())
    assistant.add_tool(SearchTool())
    assistant.start()
    
