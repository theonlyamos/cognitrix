import json
import os
from pathlib import Path

def save_tool_as_json(name: str, description: str, category: str, function_code: str):
    """Save the tool information as a JSON file."""
    tool_info = {
        "name": name,
        "description": description,
        "category": category,
        "function_code": function_code
    }
    
    tools_dir = Path("custom_tools")
    tools_dir.mkdir(exist_ok=True)
    
    file_path = tools_dir / f"{name.lower().replace(' ', '_')}.json"
    with file_path.open('w') as f:
        json.dump(tool_info, f, indent=2)

def save_tool_as_python_file(name: str, description: str, category: str, function_code: str):
    """Save the tool as a Python file."""
    tools_dir = Path("custom_tools")
    tools_dir.mkdir(exist_ok=True)
    
    file_path = tools_dir / f"{name.lower().replace(' ', '_')}.py"
    with file_path.open('w') as f:
        f.write(f"from cognitrix.tools.tool import tool\n\n")
        f.write(f"@tool(category='{category}')\n")
        f.write(f"def {name.lower().replace(' ', '_')}(*args, **kwargs):\n")
        f.write(f"    \"\"\"{description}\"\"\"\n")
        f.write(f"    {function_code.strip()}\n")

# Function to load saved tools
def load_saved_tools():
    """Load all saved tools from the custom_tools directory."""
    tools_dir = Path("custom_tools")
    if not tools_dir.exists():
        return
    
    for file_path in tools_dir.glob("*.json"):
        with file_path.open('r') as f:
            tool_info = json.load(f)
        
        create_tool(**tool_info)
    
    for file_path in tools_dir.glob("*.py"):
        module_name = file_path.stem
        spec = importlib.util.spec_from_file_location(module_name, file_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        
        # Add the loaded tool to the global namespace
        for name, obj in module.__dict__.items():
            if callable(obj) and hasattr(obj, '_is_tool'):
                globals()[name] = obj

# Call this function at startup to load all saved tools
load_saved_tools()