"""Decide which tool calls are risky enough to ask a person first.

Tasks are meant to run without anyone watching, so the policy only asks for
actions that are destructive or hard to undo:

- deleting things (files, records, branches, ...)
- overwriting an existing file
- sending or posting something outside the app (email, messages, deploys)
- shell commands (rules owned by the shell tool, see ``_shell_risk``)
- handing work to a coding agent CLI (``cognitrix.common.coding_agents.risk``)
- remote (MCP) actions whose effect we can't tell from the name or annotations

Reading, listing, searching, web search and web fetch never ask, whatever
words appear in their arguments. Earlier versions scanned the arguments for
keywords like "key", "update" or "config", which made ordinary searches ask.

The agent loop asks for MEDIUM and HIGH; LOW runs straight away.
"""

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class RiskLevel(Enum):
    """Risk levels for operations."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# Severity ordering for comparisons (string values do NOT sort by severity).
_RISK_ORDER = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2}


@dataclass
class RiskAssessment:
    """Assessment of operation risk."""
    risk_level: RiskLevel
    categories: list[str] = field(default_factory=list)
    details: str = "No specific risk detected"
    confidence: float = 1.0


def _low(details: str = "No specific risk detected", category: str | None = None) -> RiskAssessment:
    return RiskAssessment(RiskLevel.LOW, [category] if category else [], details)


# Verbs that destroy data. A tool whose name contains one of these asks.
DESTRUCTIVE_VERBS = frozenset({
    'delete', 'remove', 'rm', 'unlink', 'destroy', 'drop', 'purge', 'wipe',
    'erase', 'trash', 'truncate', 'shred', 'uninstall', 'revoke',
})

# Verbs that send something outside the app. These can't be taken back.
OUTWARD_VERBS = frozenset({
    'send', 'post', 'publish', 'reply', 'forward', 'tweet', 'share',
    'submit', 'deploy', 'transfer', 'pay', 'purchase', 'buy', 'invite',
    'comment', 'broadcast',
})

# Verbs that only look things up.
READ_VERBS = frozenset({
    'get', 'list', 'search', 'read', 'fetch', 'find', 'query', 'describe',
    'view', 'show', 'lookup', 'count', 'check', 'download', 'inspect',
    'explain', 'preview', 'summarize', 'browse', 'open', 'glob', 'grep',
    'status', 'info', 'load', 'ask', 'screenshot',
})

# Verbs that add or change things in a way that's easy to undo.
CHANGE_VERBS = frozenset({
    'create', 'add', 'new', 'update', 'edit', 'set', 'write', 'upload',
    'save', 'label', 'mark', 'unmark', 'rename', 'copy', 'draft', 'append',
    'schedule', 'assign', 'generate', 'tag', 'untag', 'star',
})

# Built-in tools with a known effect, keyed by normalised name.
_KNOWN_LOW = frozenset({
    # files (Edit is a targeted change; Write is checked separately)
    'read', 'grep', 'glob', 'edit', 'open_file',
    # web
    'search', 'tavily_search', 'webfetch', 'web_fetch', 'open_website',
    # agents, teams, tasks, skills, mcp discovery
    'list_agents', 'create_agent', 'call_agent', 'create_new_team',
    'create_task', 'assign_task', 'load_skill', 'list_mcp_tools',
    # looking at tasks, and starting or stopping a run (a stopped run can be
    # resumed; the run's own risky tool calls still ask)
    'list_tasks', 'get_task', 'start_task', 'cancel_task_run',
    'run_mcp_tool',  # the MCP action itself is checked in the agent loop
    # always-on agents: notes and alerts go to the run's own owner
    'read_notes', 'update_notes', 'schedule_next_check', 'notify_me',
    # an offer or a skill draft only waits in the person's Inbox for a yes
    'offer_help', 'propose_skill',
    # chats agents start: their own risky tool calls still ask in that chat
    'start_chat', 'check_chat', 'message_chat',
    # saved memories: each change shows in the chat with Undo
    'memory', 'search_chats', 'read_chat',
    # the app's colours: each change shows in the chat with Undo
    'set_theme',
    # artifact pages: a page is private to its owner until they share it
    # themselves, and every version is kept
    'publish_artifact', 'read_artifact', 'artifact_comments',
    # cards only show in the chat; a button press is a message to the agent
    'show_card',
    # apps: reading an app's status and logs
    'app_status',
    # asking the person a question, images, screenshots
    'ask_user', 'ask_question', 'generate_image', 'take_screenshot',
    # background computer use: looking and scrolling
    'list_windows', 'look_at_window', 'scroll_window',
    # background shells: reading output or stopping a shell the agent started
    'bash_output', 'kill_shell',
})

# Desktop control acts on the person's live screen, where a click can do
# anything (including pressing "Send"), so it keeps asking.
# Background window actions (cognitrix/computer) leave the person's screen
# alone but can still press any button in any app, so they ask the same way.
_DESKTOP_CONTROL = frozenset({
    'text_input', 'key_press', 'hot_key', 'mouse_click',
    'mouse_double_click', 'mouse_right_click', 'mouse_scroll',
    'click_element', 'set_text', 'click_in_window', 'type_in_window', 'press_key_in_window',
    # open_app starts any program or script, like a shell command would
    'open_app',
})

# The window tools act in the agents' sandboxed computer when it is on; the
# full-screen tools above always act on this machine's own screen.
_SANDBOX_WINDOW_TOOLS = frozenset({
    'click_element', 'set_text', 'click_in_window', 'type_in_window', 'press_key_in_window', 'open_app',
    'scroll_window', 'look_at_window', 'list_windows',
})


def _sandboxed_computer() -> bool:
    from cognitrix.computer.sandbox.client import sandbox_mode

    return sandbox_mode() is not None


def _publish_app_risk(params: dict) -> RiskAssessment:
    """Publishing an app runs the agent's code as a server. In the agents'
    sandboxed computer that's their own container; on this machine it's like
    running a shell command, so it asks."""
    from cognitrix.hosted_apps.runner import default_runtime

    command = str(params.get('start_command') or '')[:200]
    if default_runtime() == 'sandbox' and not params.get('app_id'):
        return RiskAssessment(RiskLevel.LOW, ['app_sandbox'], f"Runs an app in the agents' sandboxed computer: {command}")
    if default_runtime() == 'sandbox' and params.get('app_id'):
        # An existing app keeps the runtime it was made with; checked again when it runs.
        return RiskAssessment(RiskLevel.LOW, ['app_sandbox'], f'Publishes a new version of an app: {command}')
    return RiskAssessment(RiskLevel.HIGH, ['app_local'],
                          f'Runs an app on this machine as a server, with the command: {command}')


# Handing a job to a coding agent CLI (Claude Code, Codex, ...): it edits
# files and may run commands on its own, so a run always asks.
_CODING_AGENT_TOOLS = frozenset({'coding_agent'})

_SHELL_TOOLS = frozenset({'bash', 'shell', 'terminal', 'run_command', 'execute_command', 'execute_code'})

# Custom tools that run commands or code go through the shell rules too.
_SHELL_WORDS = frozenset({'shell', 'terminal', 'exec', 'execute', 'eval'})

# HTTP methods that change something on the other side.
_SENDING_METHODS = frozenset({'POST', 'PUT', 'PATCH', 'DELETE'})

_WRITE_TOOLS = frozenset({'write', 'write_file', 'overwrite_file'})

# Giving an agent a folder (or write access to one) is the person's call.
_FOLDER_REQUEST_TOOLS = frozenset({'request_folder'})


def _truthy(value: Any) -> bool:
    return value is True or str(value).strip().lower() in ('true', '1', 'yes')


def _normalise(name: str) -> str:
    return re.sub(r'[\s\-]+', '_', str(name or '').strip()).lower()


def _words(name: str) -> set[str]:
    """Split a tool name into lowercase words (snake, kebab, spaces, camelCase)."""
    spaced = re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', str(name or ''))
    return {w for w in re.split(r'[^A-Za-z0-9]+', spaced.lower()) if w}


def _name_verb_risk(name: str) -> RiskAssessment | None:
    """Risk from destructive or outward verbs in a tool name, else None."""
    words = _words(name)
    hit = words & DESTRUCTIVE_VERBS
    if hit:
        return RiskAssessment(
            RiskLevel.HIGH, ['deletion'], f"'{name}' deletes or removes data ({', '.join(sorted(hit))})"
        )
    hit = words & OUTWARD_VERBS
    if hit:
        return RiskAssessment(
            RiskLevel.MEDIUM, ['external_send'],
            f"'{name}' sends or publishes something outside the app ({', '.join(sorted(hit))})",
        )
    return None


def _resolve_tool_path(path: Any) -> Path | None:
    """Resolve a file tool's path the way the file tools do (from the primary folder)."""
    if not path or not isinstance(path, str):
        return None
    try:
        from cognitrix.common.folders import lexical_path
        return lexical_path(path).resolve()
    except Exception:
        return None


def _in_apps_folder(target: Path) -> bool:
    try:
        from cognitrix.common.folders import in_apps_root
        return in_apps_root(target)
    except Exception:
        return False


def _write_risk(params: dict) -> RiskAssessment:
    """Writing a new file or appending is fine; replacing an existing file asks."""
    if params.get('append') in (True, 'true', 'True', 1):
        return _low("Appends to a file", 'file_write')
    raw = params.get('file_path') or params.get('path') or params.get('filename')
    target = _resolve_tool_path(raw)
    if target is None:
        # Can't tell where it goes: treat as a possible overwrite.
        return RiskAssessment(RiskLevel.MEDIUM, ['file_overwrite'], "May overwrite an existing file")
    if target.exists():
        if _in_apps_folder(target):
            # Agents build apps here; changing an app's files never asks.
            return _low(f"Updates '{raw}' in the apps folder", 'file_write')
        return RiskAssessment(
            RiskLevel.MEDIUM, ['file_overwrite'], f"Overwrites the existing file '{raw}'"
        )
    return _low(f"Creates the new file '{raw}'", 'file_write')


def _shell_risk(tool_name: str, params: dict) -> RiskAssessment:
    """Risk for shell commands, using the shell tool's own rules.

    ``cognitrix.common.shell.shell_risk`` decides which commands are safe to
    run unattended. Where it isn't available every command asks, as before.
    """
    command = str(params.get('command') or params.get('code') or '')
    try:
        from cognitrix.common.shell import shell_risk  # type: ignore[attr-defined]
    except ImportError:
        shell_risk = None
    note = _read_only_folder_note(params)
    if shell_risk is not None:
        try:
            level, details = shell_risk(params)
            assessment = RiskAssessment(RiskLevel(str(level).lower()), ['shell'], details)
            if note and assessment.risk_level != RiskLevel.LOW:
                assessment = RiskAssessment(assessment.risk_level, assessment.categories, details + note)
            return assessment
        except Exception:
            pass
    return RiskAssessment(
        RiskLevel.MEDIUM, ['shell'], f"Runs a shell command: {command[:200]}{note}"
    )


def _read_only_folder_note(params: dict) -> str:
    """Warn on the approval card when a command runs in a read only folder.

    The shell can't enforce read only, so approving lets this command write.
    """
    try:
        from cognitrix.common.folders import folder_for, lexical_path, primary_folder
        raw = params.get('working_dir') or params.get('cwd')
        where = lexical_path(str(raw)) if raw else primary_folder()
        folder = folder_for(where)
    except Exception:
        return ''
    if folder is None or folder.writable:
        return ''
    return (
        f" (runs in '{folder.label}', which is read only: approving lets this "
        "command change files there)"
    )


def mcp_only_reads(tool_name: str, annotations: dict | None) -> bool:
    """True when an action on a connected app clearly changes nothing.

    Stricter than ``mcp_tool_risk``: an undoable change is still a change.
    The server has to mark the tool read-only, or its name has to say it
    reads and nothing else.
    """
    notes = annotations if isinstance(annotations, dict) else {}
    if notes.get('readOnlyHint'):
        return True
    if notes.get('readOnlyHint') is False or notes.get('destructiveHint') is True:
        return False
    words = _words(tool_name)
    return bool(words & READ_VERBS) and not words & (CHANGE_VERBS | DESTRUCTIVE_VERBS | OUTWARD_VERBS)


def mcp_tool_risk(tool_name: str, annotations: dict | None) -> RiskAssessment:
    """Risk for an action on a connected app (MCP server).

    ``annotations`` are the server's hints for the tool (``readOnlyHint``,
    ``destructiveHint``), or None when the tool hasn't been listed yet.
    """
    notes = annotations if isinstance(annotations, dict) else {}
    if notes.get('readOnlyHint'):
        return _low(f"'{tool_name}' is marked read-only by its server", 'mcp_read')
    by_name = _name_verb_risk(tool_name)
    if by_name is not None:
        return by_name
    if notes.get('destructiveHint') is True:
        return RiskAssessment(
            RiskLevel.MEDIUM, ['mcp_destructive'],
            f"'{tool_name}' is marked as possibly destructive by its server",
        )
    if notes.get('destructiveHint') is False:
        return _low(f"'{tool_name}' is marked non-destructive by its server", 'mcp_write')
    words = _words(tool_name)
    if words & (READ_VERBS | CHANGE_VERBS):
        return _low(f"'{tool_name}' reads or makes an undoable change", 'mcp_write')
    return RiskAssessment(
        RiskLevel.MEDIUM, ['mcp_unknown'],
        f"Can't tell what '{tool_name}' does from its name, and its server doesn't say",
    )


class DestructiveOpDetector:
    """Classifies tool calls by how hard they are to undo."""

    def analyze(self, tool_name: str, params: dict) -> RiskAssessment:
        """Assess a tool call. Only names and a few known parameters count."""
        params = params if isinstance(params, dict) else {}
        name = _normalise(tool_name)

        if name in _KNOWN_LOW:
            return _low()
        if name in _CODING_AGENT_TOOLS:
            from cognitrix.common.coding_agents import risk as coding_agent_risk
            level, details = coding_agent_risk(params)
            return RiskAssessment(RiskLevel(level), ['coding_agent'], details)
        if name == 'publish_app':
            return _publish_app_risk(params)
        if name in _SHELL_TOOLS or _words(tool_name) & _SHELL_WORDS or isinstance(params.get('command'), str):
            return _shell_risk(tool_name, params)
        if name in _WRITE_TOOLS:
            return _write_risk(params)
        if name in _FOLDER_REQUEST_TOOLS:
            access = 'read & write' if str(params.get('access', '')).lower() == 'write' else 'read only'
            return RiskAssessment(
                RiskLevel.MEDIUM, ['folder_access'],
                f"Wants {access} access to the folder '{params.get('path', '')}'",
            )
        if name == 'ask_to_take_over':
            # Always a person's answer: they take over, then hand back.
            reason = str(params.get('reason') or '').strip()[:200] or 'a step only you should do'
            return RiskAssessment(
                RiskLevel.MEDIUM, ['person_takeover'],
                f"Asks you to take over its computer: {reason}. Open the agent's computer, press Take over, "
                'do it, then allow this to hand the computer back')
        if name in _SANDBOX_WINDOW_TOOLS and _sandboxed_computer():
            # The agents' own computer in a container, not the person's screen.
            return RiskAssessment(RiskLevel.LOW, ['sandbox_computer'],
                                  f"'{tool_name}' works in the agent's sandboxed computer")
        if name in _DESKTOP_CONTROL and _truthy(params.get('use_screen')):
            # Takes over the person's real mouse and keyboard for a moment.
            # Never covered by a per-app "always allow".
            return RiskAssessment(
                RiskLevel.MEDIUM, ['desktop_control', 'screen_takeover'],
                f"'{tool_name}' takes over your screen, mouse and keyboard for a moment",
            )
        if name in _DESKTOP_CONTROL:
            return RiskAssessment(
                RiskLevel.MEDIUM, ['desktop_control'],
                f"'{tool_name}' controls the live desktop",
            )
        method = str(params.get('method') or '').upper()
        if method in _SENDING_METHODS:
            return RiskAssessment(
                RiskLevel.MEDIUM, ['external_send'],
                f"Sends an HTTP {method} request to an outside service",
            )

        by_name = _name_verb_risk(tool_name)
        if by_name is not None:
            return by_name
        # Everything else (reads, creates, updates, custom tools) runs.
        return _low()

    def is_destructive(self, tool_name: str, params: dict, threshold: RiskLevel = RiskLevel.MEDIUM) -> bool:
        """True if the call is at or above ``threshold``."""
        assessment = self.analyze(tool_name, params)
        return _RISK_ORDER[assessment.risk_level] >= _RISK_ORDER[threshold]
