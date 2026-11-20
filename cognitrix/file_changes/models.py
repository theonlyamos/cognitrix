"""What the changes view keeps between turns.

``FileChange``: a file an agent wrote or edited on this machine or on a paired
PC, with its text from before the chat first touched it and after its latest
change. (Changes in the sandbox are read from the sandbox's own copy, so they
have no rows here.)

``FileReview``: the hunks the person accepted, for any kind of change.
"""
from odbms import Model

LOCAL = 'local'
PC = 'pc'
SANDBOX = 'sandbox'


class FileChange(Model):
    session_id: str
    user_id: str = ''
    agent_id: str = ''
    task_id: str = ''
    location: str = LOCAL
    """'local' (the machine Cognitrix runs on) or 'pc' (a paired PC)."""
    device_id: str = ''
    path: str
    """The file's full path where it lives."""
    before: str = ''
    existed: bool = True
    """False when the chat created the file."""
    after: str = ''
    updated: str = ''


class FileReview(Model):
    session_id: str
    key: str
    """'<source key>|<path>'."""
    accepted: str = ''
    """Comma separated hunk ids."""


MODELS = (FileChange, FileReview)
