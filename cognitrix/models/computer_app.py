"""A person's standing answer for one app in background computer use:
always allow agents to act in it, or never."""
from odbms import Model


class ComputerAppPermission(Model):
    user_id: str
    app: str
    """Executable name in lower case, e.g. 'notepad.exe'."""
    level: str = 'allow'
    """'allow' or 'deny'. No row means ask each time."""
