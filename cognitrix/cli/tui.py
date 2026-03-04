"""
Textual-based Terminal User Interface (TUI) for Cognitrix.
Minimal/Modern design with Catppuccin-inspired theme.
"""
import asyncio
from typing import Any

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, VerticalScroll, Vertical
from textual.widgets import Button, Footer, Header, Input, Markdown, Static
from textual.reactive import reactive
from textual.binding import Binding

from cognitrix.agents.base import Agent
from cognitrix.sessions.base import Session

CSS = """
CognitrixApp {
    background: #1e1e2e;
}

Header {
    background: #181825;
    color: #cdd6f4;
}

#app-container {
    layout: horizontal;
    height: 1fr;
}

#sidebar {
    width: 32;
    height: 100%;
    background: #181825;
    border-right: solid #313244;
}

#sidebar.hidden {
    display: none;
}

.sidebar-header {
    height: auto;
    padding: 1 2;
    background: #181825;
    border-bottom: solid #313244;
}

.agent-name {
    color: #89b4fa;
    text-style: bold;
}

.agent-description {
    color: #6c7086;
    padding-top: 1;
}

#sidebar-tools {
    height: 1fr;
    padding: 1 2;
}

.sidebar-section-title {
    color: #6c7086;
    text-style: bold;
    margin-bottom: 1;
}

.tool-item {
    color: #a6e3a1;
    padding: 0 1;
}

#main-area {
    width: 1fr;
    height: 100%;
}

#chat-container {
    height: 1fr;
}

#chat-area {
    height: auto;
    padding: 0 2;
}

.chat-bubble {
    width: 100%;
    height: auto;
    padding: 1 2;
    margin: 1 0;
}

.chat-bubble.user {
    background: #313244;
    border-left: none;
    border-right: solid #89b4fa;
    margin-left: 6;
}

.chat-bubble.agent {
    background: #232330;
    border-left: solid #89b4fa;
    border-right: none;
    margin-right: 6;
}

.chat-bubble.system {
    background: #232330;
    border: solid #45475a;
    margin: 1 3;
    text-align: center;
}

.chat-role {
    color: #6c7086;
    text-style: bold;
    margin-bottom: 1;
}

.chat-content {
    color: #cdd6f4;
}

#welcome-splash {
    width: 100%;
    height: 1fr;
    content-align: center middle;
}

#welcome-splash.hidden {
    display: none;
}

.welcome-logo {
    color: #89b4fa;
    text-align: center;
    text-style: bold;
}

.welcome-version {
    color: #6c7086;
    text-align: center;
    margin-top: 1;
}

.welcome-desc {
    color: #a6adc8;
    text-align: center;
    margin-top: 1;
}

.welcome-hints {
    color: #585b70;
    text-align: center;
    margin-top: 2;
}

#input-container {
    height: auto;
    dock: bottom;
    background: #181825;
    border-top: solid #313244;
    padding: 1 2;
}

#input-wrapper {
    height: auto;
}

Input {
    background: #232330;
    color: #cdd6f4;
    border: solid #45475a;
    padding: 0 2;
    width: 1fr;
}

Input:focus {
    border: solid #89b4fa;
}

#command-palette {
    display: none;
    layer: overlay;
    width: 100%;
    height: 100%;
}

#command-palette.visible {
    display: block;
}

#palette-modal {
    width: 60;
    height: auto;
    max-height: 20;
    background: #1e1e2e;
    border: solid #45475a;
    padding: 1;
    align: center middle;
}

#palette-input {
    width: 100%;
    margin-bottom: 1;
}

#palette-results {
    height: auto;
    max-height: 15;
    padding: 0 1;
}

.palette-item {
    padding: 0 2;
    color: #cdd6f4;
}

.palette-item-title {
    text-style: bold;
}

.palette-item-desc {
    color: #6c7086;
}

.status-dot {
    color: #a6e3a1;
    text-style: bold;
}

Footer {
    background: #181825;
    color: #6c7086;
}
"""

WELCOME_ART = (
    "  ██████╗  ██████╗  ██████╗ ███╗   ██╗██╗████████╗██████╗ ██╗██╗  ██╗\n"
    " ██╔════╝██╔═══██╗██╔════╝ ████╗  ██║██║╚══██╔══╝██╔══██╗██║╚██╗██╔╝\n"
    " ██║     ██║   ██║██║  ███╗██╔██╗ ██║██║   ██║   ██████╔╝██║ ╚███╔╝\n"
    " ██║     ██║   ██║██║   ██║██║╚██╗██║██║   ██║   ██╔══██╗██║ ██╔██╗\n"
    " ╚██████╗╚██████╔╝╚██████╔╝██║ ╚████║██║   ██║   ██║  ██║██║██╔╝ ██╗\n"
    "  ╚═════╝ ╚═════╝  ╚═════╝ ╚═╝  ╚═══╝╚═╝   ╚═╝   ╚═╝  ╚═╝╚═╝╚═╝  ╚═╝"
)

COMMANDS = [
    {"id": "help", "title": "/help", "desc": "Show available commands", "shortcut": "Ctrl+H"},
    {"id": "clear", "title": "/clear", "desc": "Clear chat history", "shortcut": "Ctrl+L"},
    {"id": "tools", "title": "/tools", "desc": "List available tools", "shortcut": "Ctrl+T"},
    {"id": "agents", "title": "/agents", "desc": "List all agents"},
    {"id": "history", "title": "/history", "desc": "Show chat history", "shortcut": "Ctrl+↑"},
    {"id": "switch", "title": "/switch", "desc": "Switch to another agent"},
    {"id": "mcp", "title": "/mcp", "desc": "List MCP servers"},
    {"id": "mcp-tools", "title": "/mcp-tools", "desc": "List MCP tools"},
    {"id": "add-agent", "title": "/add agent", "desc": "Create a new agent"},
    {"id": "toggle-sidebar", "title": "Toggle Sidebar", "desc": "Show/hide sidebar", "shortcut": "Ctrl+B"},
]


class ChatBubble(Static):
    def __init__(self, content: str, role: str):
        super().__init__()
        self.raw_content = content
        self.role = role
        self.add_class("chat-bubble")
        self.add_class(role)

    def compose(self) -> ComposeResult:
        role_label = "You" if self.role == "user" else ("Assistant" if self.role == "agent" else "System")
        
        if self.role == "agent":
            yield Static(role_label, classes="chat-role")
            yield Markdown(self.raw_content, classes="chat-content")
        else:
            yield Static(role_label, classes="chat-role")
            yield Static(self.raw_content, classes="chat-content")

    def update_content(self, new_chunk: str):
        self.raw_content += new_chunk
        if self.role == "agent":
            for md in self.query(Markdown):
                md.update(self.raw_content)
        else:
            for content in self.query(Static):
                if "chat-content" in content.classes:
                    content.update(self.raw_content)


class CommandPalette(Container):
    def __init__(self, on_select, on_close):
        super().__init__()
        self.on_select_callback = on_select
        self.on_close_callback = on_close
        self.filtered_commands = COMMANDS
        self.selected_index = 0

    def compose(self) -> ComposeResult:
        with Vertical(id="palette-modal"):
            yield Input(placeholder="Type a command...", id="palette-input")
            yield Vertical(id="palette-results")

    def on_mount(self) -> None:
        self.update_results()

    def on_input_changed(self, event: Input.Changed) -> None:
        self.filter_commands(event.value)

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        await self.select_current()

    def filter_commands(self, query: str):
        if not query:
            self.filtered_commands = COMMANDS
        else:
            query_lower = query.lower()
            self.filtered_commands = [
                cmd for cmd in COMMANDS
                if query_lower in cmd["title"].lower() or query_lower in cmd["desc"].lower()
            ]
        self.selected_index = 0
        self.update_results()

    def update_results(self):
        results = self.query_one("#palette-results", Vertical)
        results.remove_children()
        
        for i, cmd in enumerate(self.filtered_commands):
            is_selected = i == self.selected_index
            item = Static(
                f"[b]{cmd['title']}[/b]  {cmd['desc']}" + (f"  ({cmd.get('shortcut', '')})" if cmd.get('shortcut') else ""),
                classes="palette-item" + (" selected" if is_selected else "")
            )
            results.mount(item)

    def move_selection(self, delta: int):
        if self.filtered_commands:
            self.selected_index = (self.selected_index + delta) % len(self.filtered_commands)
            self.update_results()

    async def select_current(self):
        if self.filtered_commands and 0 <= self.selected_index < len(self.filtered_commands):
            cmd = self.filtered_commands[self.selected_index]
            await self.on_select_callback(cmd)


class CognitrixApp(App):
    CSS = CSS
    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit"),
        Binding("ctrl+b", "toggle_sidebar", "Toggle Sidebar"),
        Binding("ctrl+k", "show_command_palette", "Command Palette"),
        Binding("ctrl+l", "clear_chat", "Clear Chat"),
        Binding("ctrl+t", "show_tools", "Show Tools"),
        Binding("escape", "close_modal", "Close", show=False),
        Binding("enter", "send_message", "Send", show=False),
    ]

    sidebar_visible = reactive(True)
    command_palette_visible = reactive(False)

    def __init__(self, agent: Agent, session: Session):
        super().__init__()
        self.agent = agent
        self.app_session = session
        self.active_message = None
        self.streaming = False

    def compose(self) -> ComposeResult:
        yield Header()
        
        with Horizontal(id="app-container"):
            with Container(id="sidebar"):
                yield Container(
                    Static(self.agent.name, classes="agent-name"),
                    Static(getattr(self.agent, 'description', 'AI Assistant') or "AI Assistant", classes="agent-description"),
                    classes="sidebar-header"
                )
                
                with VerticalScroll(id="sidebar-tools"):
                    yield Static("Tools", classes="sidebar-section-title")
                    if self.agent.tools:
                        for tool in self.agent.tools:
                            yield Static(f"→ {tool.name}", classes="tool-item")
                    else:
                        yield Static("No tools available", classes="tool-item")

            with Container(id="main-area"):
                with Container(id="welcome-splash"):
                    yield Static(WELCOME_ART, classes="welcome-logo")
                    yield Static("v0.2.5", classes="welcome-version")
                    yield Static("AI Agent Framework", classes="welcome-desc")
                    yield Static(
                        "Ctrl+K  Command Palette  │  Ctrl+B  Toggle Sidebar  │  Ctrl+L  Clear",
                        classes="welcome-hints"
                    )

                with VerticalScroll(id="chat-container"):
                    with Vertical(id="chat-area"):
                        pass

                with Container(id="input-container"):
                    with Container(id="input-wrapper"):
                        yield Input(placeholder="Ask anything... (Enter to send)", id="message-input")

        with Container(id="command-palette"):
            yield CommandPalette(self.handle_command_select, self.hide_command_palette)

        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#message-input").focus()
        
        # If there's existing chat history, hide the welcome splash
        if self.app_session.chat:
            self.query_one("#welcome-splash").add_class("hidden")

    def watch_sidebar_visible(self, visible: bool):
        sidebar = self.query_one("#sidebar")
        if visible:
            sidebar.remove_class("hidden")
        else:
            sidebar.add_class("hidden")

    def action_toggle_sidebar(self):
        self.sidebar_visible = not self.sidebar_visible

    def action_show_command_palette(self):
        self.command_palette_visible = True
        palette = self.query_one("#command-palette")
        palette.add_class("visible")
        self.query_one("#palette-input", Input).focus()

    def action_close_modal(self):
        if self.command_palette_visible:
            self.hide_command_palette()

    def hide_command_palette(self):
        self.command_palette_visible = False
        palette = self.query_one("#command-palette")
        palette.remove_class("visible")
        self.query_one("#message-input").focus()

    async def handle_command_select(self, cmd: dict):
        self.hide_command_palette()
        
        if cmd["id"] == "help":
            self.show_help()
        elif cmd["id"] == "clear":
            self.action_clear_chat()
        elif cmd["id"] == "tools":
            self.show_tools_list()
        elif cmd["id"] == "history":
            self.show_history()
        elif cmd["id"] == "toggle-sidebar":
            self.action_toggle_sidebar()

    def action_clear_chat(self):
        chat_area = self.query_one("#chat-area", Vertical)
        chat_area.remove_children()
        self.add_system_message("Chat cleared.")

    def action_show_tools(self):
        self.show_tools_list()

    def show_tools_list(self):
        if self.agent.tools:
            tools_text = "\n".join(f"• {tool.name}: {tool.description or 'No description'}" for tool in self.agent.tools)
        else:
            tools_text = "No tools available."
        self.add_system_message(f"**Available Tools:**\n{tools_text}")

    def show_history(self):
        if self.app_session.chat:
            history_lines = []
            for msg in self.app_session.chat:
                role = msg.get("role", "")
                content = msg.get("content", "")
                if content and role != "system":
                    speaker = "You" if role == "user" else self.agent.name
                    history_lines.append(f"**{speaker}:** {content[:100]}{'...' if len(content) > 100 else ''}")
            
            if history_lines:
                self.add_system_message("**Chat History:**\n" + "\n".join(history_lines))
            else:
                self.add_system_message("No chat history.")
        else:
            self.add_system_message("No chat history.")

    def show_help(self):
        help_text = """
**Commands:**
`/help` - Show this message
`/clear` - Clear chat history
`/tools` - List available tools
`/history` - Show chat history

**Keyboard Shortcuts:**
`Ctrl+B` - Toggle sidebar
`Ctrl+K` - Command palette
`Ctrl+L` - Clear chat
`Ctrl+T` - Show tools
`Escape` - Close modals
"""
        self.add_system_message(help_text)

    def add_system_message(self, content: str):
        chat_area = self.query_one("#chat-area", Vertical)
        msg = ChatBubble(content, "system")
        chat_area.mount(msg)
        chat_area.scroll_end(animate=False)

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        
        event.input.value = ""
        await self.send_message(text)


    async def send_message(self, text: str):
        if text.lower() in ("q", "quit", "exit"):
            self.exit()
            return

        # Hide welcome splash on first message
        splash = self.query_one("#welcome-splash")
        if not splash.has_class("hidden"):
            splash.add_class("hidden")

        chat_area = self.query_one("#chat-area", Vertical)
        
        user_bubble = ChatBubble(text, "user")
        await chat_area.mount(user_bubble)
        user_bubble.scroll_visible(animate=False)
        
        self.active_message = ChatBubble("", "agent")
        await chat_area.mount(self.active_message)
        self.active_message.scroll_visible(animate=False)
        
        self.streaming = True

        def tui_stream_output(text_chunk: str, *args, **kwargs):
            if isinstance(text_chunk, str):
                self.call_from_thread(self._update_active_message, text_chunk)

        def run_session_sync():
            """Run session in a worker thread so call_from_thread works."""
            import asyncio as _asyncio
            loop = _asyncio.new_event_loop()
            try:
                loop.run_until_complete(
                    self.app_session(
                        text,
                        self.agent,
                        interface="cli",
                        stream=True,
                        output=tui_stream_output
                    )
                )
            except Exception as e:
                self.call_from_thread(self._add_error, str(e))
            finally:
                loop.close()
                self.call_from_thread(self._finish_streaming)

        self.run_worker(run_session_sync, thread=True)

    def _update_active_message(self, chunk: str):
        if self.active_message:
            self.active_message.update_content(chunk)
            self.active_message.scroll_visible(animate=False)

    def _add_error(self, error: str):
        self.add_system_message(f"**Error:** {error}")

    def _finish_streaming(self):
        self.streaming = False

