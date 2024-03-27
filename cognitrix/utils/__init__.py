from PIL import Image
from typing import Dict
from cognitrix.tools import Tool
import logging
import base64
import io

json_return_format: str = """
Your response should be in a valid json format which can
be directed converted into a python dictionary with  
json.loads()
Return the response in the following format only:    
{
"type": "final_answer",
"result": "
}
if it's the final anwers or
{
"type": "function_call",
"function": "",
"arguments": []
}

Do not include the json decorator in the response.
"""

def image_to_base64(image: Image.Image) -> str:
    """Converts a PIL Image to base64"""

    screenshot_bytes = io.BytesIO()
    image.save(screenshot_bytes, format='JPEG')

    # Convert the BytesIO object to base64
    return base64.b64encode(screenshot_bytes.getvalue()).decode('utf-8')

import json

def tool_to_functions(tool: Tool) -> Dict:
    """
    Converts an instance of the Tool class to a JSON string in the OpenAI API format.
    
    Args:
        tool (Tool): An instance of the Tool class.
        
    Returns:
        str: A JSON string representing the tool in the OpenAI API format.
    """
    tool_json = {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    }
    
    # Add parameters and required fields from the tool's run() method signature
    import inspect
    run_signature = inspect.signature(tool.run)
    for param_name, param in run_signature.parameters.items():
        if param.default is param.empty:
            tool_json["function"]["parameters"]["required"].append(param_name)
        param_type = str(param.annotation) if param.annotation != param.empty else "string"
        tool_json["function"]["parameters"]["properties"][param_name] = {
            "type": param_type,
            "description": param_name
        }
        if param_type == "str" and hasattr(param.annotation, "__args__"):
            tool_json["function"]["parameters"]["properties"][param_name]["enum"] = list(param.annotation.__args__)
    
    return tool_json

def extract_json(content: str) -> dict | str:
        """
        Extract JSON content from a response string.

        Args:
            content (str): The response string to extract JSON from.

        Returns:
            dict|str: Result of the extraction.
        """
        try:
            # Escape special characters in the input string
            # escaped_content = re.escape(content)

            # Find the start and end index of the JSON string
            start_index = content.find('{')
            end_index = content.rfind('}') + 1
            
            # Extract the JSON string
            json_str = content[start_index:end_index]
            json_str = json_str.replace(r'```', '')
            
            with open('output.json', 'w') as file:
                print(json_str, file=file)
            # Convert the JSON string to a Python dictionary
            json_dict = json.loads(json_str)
            
            return json_dict
        except Exception as e:
            # logging.warning(str(e))
            return content