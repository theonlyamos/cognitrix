"""Request Folder: let an agent ask for another folder, or write access to one."""

from cognitrix.common.folders import (
    READ,
    WRITE,
    FolderError,
    active_folders,
    folder_for,
    grant_folder,
    validate_folder,
)
from cognitrix.tools.tool import tool
from cognitrix.tools.utils import ToolOutcome


@tool(category='system', retryable=False, max_attempts=1, approval_mode='always',
      supported_interfaces=['web', 'ws', 'task'])
def request_folder(path: str, access: str = 'read', reason: str = '') -> ToolOutcome:
    """Ask the user to add a folder to this chat, or to give write access to one.

    Use it when you need files outside your working folders, or need to change
    files in a read only folder. The user approves or denies it first.

    Args:
        path (str): Full path of the folder on this machine.
        access (str): 'read' (look only) or 'write' (create and change files).
        reason (str): One sentence on why you need it, shown to the user.
    """
    access = WRITE if str(access or '').strip().lower() == WRITE else READ
    try:
        folder = validate_folder(path, access)
    except FolderError as exc:
        return ToolOutcome.failure('folder_not_allowed', str(exc))
    current = folder_for(folder.root, active_folders())
    if current is not None and (current.writable or access == READ):
        return ToolOutcome.success(
            f"'{folder.path}' is already available ({'read & write' if current.writable else 'read only'})."
        )
    if not grant_folder(folder):
        return ToolOutcome.failure(
            'not_in_chat', 'Folders can only be added to a chat or task run',
        )
    what = 'read & write' if access == WRITE else 'read only'
    return ToolOutcome.success(f"Added '{folder.path}' to this chat ({what}). You can use it now.")
