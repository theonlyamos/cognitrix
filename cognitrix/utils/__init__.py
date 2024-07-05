from PIL import Image
from typing import Dict
import xml.etree.ElementTree as ET
from cognitrix.tools import Tool
import base64
import io
import re

json_return_format: str = """
Your response should be in a valid json format which can
be directly converted into a python dictionary with  
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
"type": "tool_calls",
"tool_calls": [{
"name": "<tool_name>",
"arguments": {}
}]
}

Do not include the json decorator in the response.
"""

xml_return_format: str = """```xml
<response>
    <observation>[Description of the user's request or the current situation]</observation>
    <mind-space>
        [Multi-dimensional representations of the problem, each on a new line]
    </mind-space>
    <thought>[Step-by-step reasoning process, with each step on a new line]</thought>
    <type>[Either "final_answer" or "tool_calls"]</type>
    <result>[The final answer, if applicable]</result>
    <tool_calls>
        <tool>
            <name>[Name of the tool to be called]</name>
            <arguments>
                <[argument_name]>[argument_value]</[argument_name]>
                <!-- Repeat for each argument -->
            </arguments>
        </tool>
        <!-- Repeat <tool> element for multiple tool calls -->
    </tool_calls>
    <artifacts>
        <artifact>
            <identifier>[Unique identifier for the artifact]</identifier>
            <type>[MIME type of the artifact content]</type>
            <title>[Brief title or description of the content]</title>
            <content>[The actual content of the artifact]</content>
        </artifact>
        <!-- Repeat <artifact> element for multiple artifacts -->
    </artifacts>
</response>
```
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

import json
import logging

# def extract_json(content: str) -> dict | str:
#     """
#     Extract JSON content from a response string.

#     Args:
#         content (str): The response string to extract JSON from.

#     Returns:
#         dict|str: Extracted JSON as a dictionary if valid, otherwise the original string.
#     """
#     # Check if the content contains JSON-like structure
#     if '{' not in content or '}' not in content:
#         return content

#     try:
#         # Attempt to directly parse the content as JSON
#         return json.loads(content)
#     except json.JSONDecodeError:
#         # If direct parsing fails, try to extract the JSON part
#         start_index = content.find('{')
#         end_index = content.rfind('}') + 1

#         json_str = content[start_index:end_index]

#         # Clean up the JSON string
#         json_str = json_str.replace('\n', '').replace('\\n', '').replace("'", "\"")

#         try:
#             return json.loads(json_str)
#         except json.JSONDecodeError:
#             # Attempt to further clean and parse the JSON string
#             try:
#                 # Remove any trailing commas
#                 json_str = json_str.rstrip(', ')
#                 # Replace any invalid escape sequences
#                 json_str = json_str.replace('\\"', '"').replace('\\\'', "'")
#                 return json.loads(json_str)
#             except json.JSONDecodeError:
#                 return content
#     except Exception as e:
#         # Log the exception if needed
#         logging.exception(e)
#         return content

def extract_json(content: str) -> dict | str:
        """
        Extract JSON content from a response string.

        Args:
            content (str): The response string to extract JSON from.

        Returns:
            dict|str: Result of the extraction.
        """
        # print(rf"{content}")
        default_content = content
        
        if '{' not in content:
            return content
        try:
            start_index = content.find('{')
            end_index = content.rfind('}') + 1
            # Extract the JSON string
            content = content[start_index:end_index]
            return json.loads(content)
        # except json.JSONDecodeError:
        #     print(content)
        #     # Split the JSON string into key-value pairs
        #     pairs = content.split('\n')

        #     # Initialize an empty list to store the escaped pairs
        #     escaped_pairs = []
            
        #     # Iterate over the key-value pairs
        #     for pair in pairs:
        #         # Split the pair into key and value
        #         pair = pair.strip()
                
        #         if pair:
        #             key, value = pair.split('":')
                    
        #             # Remove any whitespace from the key and value
        #             key = key.strip()
        #             q_mark = '"' if key.startswith('"') else "'"
        #             a_mark = '"' if key.startswith("'") else "'"
        #             key = key.replace(q_mark, '')
        #             value = value.strip()
                    
        #             if not value.startswith('[') and not value.endswith('{'):
        #                 start_index = value.find('"') + 1
        #                 end_index = value.rfind('"')
        #                 value = value[start_index:end_index]
        #                 # value = value.replace(a_mark, f'\{a_mark}').replace(q_mark, f"\{q_mark}")
        #             else:
        #                 value = value.replace('\n', '')
        #                 value = json.loads(value)

        #             # If the value is a string, escape any double quotes
        #             if isinstance(value, str):
        #                 if value.startswith(q_mark) and value.endswith(q_mark):
        #                     quote_start = value.find(q_mark)
        #                     quote_end = value.rfind(q_mark)
        #                     for i, char in enumerate(value):
        #                         if char == q_mark and (i != quote_start and i != quote_end):
        #                             value = value.replace(q_mark, f'\\{q_mark}', 1)
        #                         break

        #             # Add the escaped pair to the list
        #             escaped_pairs.append([key, value])
            
        #     return dict(escaped_pairs)
        except Exception as e:
            # logging.exception(e)
            return default_content
    
def extract_parts(text):
    pattern = r'(.*?)<response>(.*?)</response>(.*)'
    match = re.search(pattern, text, re.DOTALL)
    
    if match:
        before = match.group(1).strip()
        response = match.group(2).strip()
        after = match.group(3).strip()
        return before, response, after
    else:
        return None, None, None

def xml_to_dict(xml_string) -> dict | str:
    """
    Convert an XML string to a Python dictionary.

    Args:
        xml_string (str): The XML string to convert.

    Returns:
        dict: A dictionary representation of the XML string.

    Raises:
        None

    This function removes the ```xml and ``` decorators from the XML string if present. It then strips any leading or trailing whitespace from the XML string. The function uses the `xml.etree.ElementTree` module to parse the XML string into an `Element` object. The `parse_element` function is defined to recursively parse the XML tree and convert it into a dictionary representation. The function returns a dictionary with the root tag as the key and the parsed XML tree as the value.

    Example:
        >>> xml_string = '<root><child>Hello</child></root>'
        >>> xml_to_dict(xml_string)
        {'root': {'child': 'Hello'}}
    """
    try:
        before, extracted, after = extract_parts(xml_string)
        
        if extracted:
            xml_string = extracted
        xml_string = xml_string.strip()
        if xml_string.startswith("```xml"):
            xml_string = xml_string[6:]
        if xml_string.endswith("```"):
            xml_string = xml_string[:-3]
        
        xml_string = xml_string.strip()
        
        root = ET.fromstring(xml_string)
        
        def parse_element(element):
            result = {}
            if element.text and element.text.strip():
                return element.text.strip()
            for child in element:
                child_data = parse_element(child)
                if child.tag in result:
                    if isinstance(result[child.tag], list):
                        result[child.tag].append(child_data)
                    else:
                        result[child.tag] = [result[child.tag], child_data]
                else:
                    result[child.tag] = child_data
            result['before'] = before
            result['after'] = after
            return result
        
        return {root.tag: parse_element(root)}
    except Exception as e:
        # logging.exception(e)
        return xml_string