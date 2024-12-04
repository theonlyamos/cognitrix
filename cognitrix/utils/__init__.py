from PIL import Image
from typing import Dict
import xml.etree.ElementTree as ET

from bs4 import BeautifulSoup
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
"type": "result",
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

xml_return_format: str = """
    <observation>[Description of the user's request or the current situation]</observation>
    <mindspace>
        [Multi-dimensional representations of the problem, each on a new line]
    </mindspace>
    <thought>[Step-by-step reasoning process, with each step on a new line]</thought>
    <type>[Either "result" or "tool_calls"]</type>
    <result>[The result, if applicable]</result>
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
    # Add parameters and required fields from the tool's run() method signature
    import inspect
    
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
    pattern = r'(.*?)<response>(.*?)(.*)'
    
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
    xml_string = '<root><child>Hello</child></root>'
    xml_to_dict(xml_string)
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
        
        xml_string = f"<response>{xml_string.strip()}</response>"
        
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

            if element.tag.lower() == 'response':
                result['before'] = before
                result['after'] = after
            
            return result
        
        return {root.tag: parse_element(root)}
    except Exception as e:
        # logging.exception(e)
        return xml_string

def item_to_xml(item):
    elem = ET.Element('tool')
    name = ET.Element('name')
    name.text = str(item.tool_name)
    value = ET.Element('result')
    value.text = str(item.content)
    elem.append(name)
    elem.append(value)

    return elem

def parse_tool_call_results(lst):
    root = ET.Element('tool_call_results')
    for item in lst:
        root.append(item_to_xml(item))
    return ET.tostring(root, encoding='unicode', method='xml')


def extract_tool_calls(data):
    # Find all tool_call sections
    pattern = r'<type>tool_call</type>.*?(?=<type>tool_call</type>|$)'
    matches = re.findall(pattern, data, re.DOTALL)
    
    tool_calls = []
    for match in matches:
        # Extract <name>
        name_match = re.search(r'<name>(.*?)</name>', match, re.DOTALL)
        name = name_match.group(1).strip() if name_match else ''
        
        # Extract <arguments> section
        args_match = re.search(r'<arguments>(.*?)</arguments>', match, re.DOTALL)
        args_section = args_match.group(1).strip() if args_match else ''
        
        # Extract all key-value pairs within <arguments>
        arguments = {}
        # Find all patterns like <tag>content</tag>
        tag_pattern = r'<(\w+)>(.*?)</\1>'
        for tag in re.finditer(tag_pattern, args_section, re.DOTALL):
            tag_name = tag.group(1)
            tag_content = tag.group(2).strip()
            # Handle special characters in content
            # tag_content = tag_content.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            arguments[tag_name] = tag_content
        
        tool_calls.append({
            'type': 'tool_call',
            'name': name,
            'arguments': arguments
        })
    
    return tool_calls

def extract_sections(data):
    section_types = ['observation', 'thought', 'mindspace', 'reflection', 'text', 'artifact']
    soup = BeautifulSoup(data, 'html.parser')
    sections = []
    result_texts = []
    
    # Function to convert a tag and its children to a dictionary
    def tag_to_dict(tag):
        content_dict = {}
        for child in tag.children:
            if child.name:
                if child.name in content_dict:
                    # If the tag appears multiple times, store as a list
                    if isinstance(content_dict[child.name], list):
                        content_dict[child.name].append(child.get_text(strip=True))
                    else:
                        content_dict[child.name] = [content_dict[child.name], child.get_text(strip=True)]
                else:
                    content_dict[child.name] = child.get_text(strip=True)
            else:
                # Add text content directly
                content = child.strip()
                if content:
                    content_dict = content
        return content_dict
    
    # Initialize result section
    result_content = []
    
    # Iterate through all root-level children
    for child in soup.children:
        if child.name in section_types: # type: ignore
            # Extract section type content
            content = tag_to_dict(child)
            sections.append({
                'type': child.name, # type: ignore
                child.name: content # type: ignore
            })
        elif isinstance(child, str):
            # Collect text outside of section types
            stripped_text = child.strip()
            if stripped_text:
                result_content.append(stripped_text)
    
    # Add result section if there is any collected text
    if result_content:
        sections.append({
            'type': 'text',
            'text': ' '.join(result_content)
        })
    
    return sections
