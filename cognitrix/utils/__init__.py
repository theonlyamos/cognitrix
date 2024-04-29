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
"observation": "Observations made by the ai agent"
"thought": "Thoughts of the ai agent on a task. Should include steps for completing the task.",
"type": "final_answer",
"result": "
}
if it's the final anwers or
{
"observation": "Observations made by the ai agent"
"thought": "Thoughts of the ai agent on a task. Should include steps for completing the task.",
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
        default_content = content
        try:
            if '{' not in content:
                return content
            
            start_index = content.find('{') + 1
            end_index = content.rfind('}')

            # Extract the JSON string
            content = content[start_index:end_index]

            # Split the JSON string into key-value pairs
            pairs = content.split('\n')

            # Initialize an empty list to store the escaped pairs
            escaped_pairs = []
            
            # Iterate over the key-value pairs
            for pair in pairs:
                # Split the pair into key and value
                pair = pair.strip()
                
                if pair:
                    key, value = pair.split('":')
                    
                    # Remove any whitespace from the key and value
                    key = key.strip()
                    q_mark = '"' if key.startswith('"') else "'"
                    a_mark = '"' if key.startswith("'") else "'"
                    key = key.replace(q_mark, '')
                    value = value.strip()
                    # print(key, value)
                    
                    if not value.startswith('[') and not value.endswith('{'):
                        start_index = value.find('"') + 1
                        end_index = value.rfind('"')
                        value = value[start_index:end_index]
                        value = value.replace('"', '\"')
                        value = value.replace("'", "\'")
                    else:
                        value = value.replace('\n', '')
                        value = value.replace(q_mark, a_mark)
                        value = json.loads(value)

                    # If the value is a string, escape any double quotes
                    if isinstance(value, str):
                        if value.startswith('"') and value.endswith('"'):
                            quote_start = value.find('"')
                            quote_end = value.rfind('"')
                            for i, char in enumerate(value):
                                if char == '"' and (i != quote_start and i != quote_end):
                                    value = value.replace('"', '\\"', 1)
                                break

                    # Add the escaped pair to the list
                    escaped_pairs.append([key, value])
            
            return dict(escaped_pairs)
        except Exception as e:
            # logging.exception(e)
            return content