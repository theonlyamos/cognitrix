from typing import Dict, Optional
from cognitrix.utils.ws import WebSocketManager

_websocket_managers: Dict[str, WebSocketManager] = {}

def register_websocket_manager(task_id: str, manager: WebSocketManager):
    _websocket_managers[task_id] = manager

def unregister_websocket_manager(task_id: str):
    if task_id in _websocket_managers:
        del _websocket_managers[task_id]

def get_websocket_manager(task_id: str) -> Optional[WebSocketManager]:
    return _websocket_managers.get(task_id)