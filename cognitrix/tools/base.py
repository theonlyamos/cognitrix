import inspect
import logging

from cognitrix.models.tool import Tool


class ToolManager:
    """Manages the discovery and retrieval of tools."""

    @staticmethod
    def list_all_tools() -> list[Tool]:
        """List all available tools."""
        tools: list[Tool] = []
        try:
            # Import the tools package to inspect its members
            module = __import__('cognitrix.tools', fromlist=['__init__'])

            # Find tool instances defined directly in the package
            func_tools = [
                f[1] for f in inspect.getmembers(module)
                if isinstance(f[1], Tool)
            ]
            tools.extend(func_tools)

            # Find tool classes and create instances
            class_tools = [
                f[1]() for f in inspect.getmembers(module, inspect.isclass)
                if issubclass(f[1], Tool) and f[1] is not Tool
            ]
            tools.extend(class_tools)

            # Return a list of unique tools, preferring instances over class defaults
            return list({t.name: t for t in tools}.values())
        except Exception as e:
            logging.exception(e)
            return tools

    @staticmethod
    def get_tools_by_category(category: str) -> list[Tool]:
        """Retrieve all tools by category."""
        if category.lower() == 'all':
            return ToolManager.list_all_tools()

        all_tools = ToolManager.list_all_tools()
        return [tool for tool in all_tools if tool.category.lower() == category.lower()]

    @staticmethod
    def get_by_name(name: str) -> Tool | None:
        """Retrieve a tool by its name."""
        all_tools = ToolManager.list_all_tools()
        return next((tool for tool in all_tools if tool.name.lower() == name.lower().replace('_', ' ')), None)

    @staticmethod
    async def get_by_user_id(user_id: str) -> list[Tool]:
        """Retrieve all tools by user ID from the database."""
        # This assumes Tool is an odbms.Model and can be queried this way
        return await Tool.find({"user_id": user_id})
