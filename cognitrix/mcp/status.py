"""
Connection status tracking for MCP servers.
Provides persistent storage and management of connection states.
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger('cognitrix.log')

# Global connection status tracking
_connection_status: Dict[str, Dict[str, Any]] = {}
_status_file_path = Path.home() / ".cognitrix" / "mcp_connection_status.json"

def _ensure_status_dir():
    """Ensure the status directory exists"""
    _status_file_path.parent.mkdir(parents=True, exist_ok=True)

def _load_connection_status():
    """Load connection status from disk"""
    global _connection_status
    try:
        if _status_file_path.exists():
            with open(_status_file_path, 'r') as f:
                _connection_status = json.load(f)
    except Exception as e:
        logger.warning(f"Could not load connection status: {e}")
        _connection_status = {}

def _save_connection_status():
    """Save connection status to disk"""
    try:
        _ensure_status_dir()
        with open(_status_file_path, 'w') as f:
            json.dump(_connection_status, f, indent=2)
    except Exception as e:
        logger.warning(f"Could not save connection status: {e}")

def update_connection_status(server_name: str, connected: bool, info: Optional[Dict[str, Any]] = None):
    """Update connection status for a server"""
    global _connection_status
    
    if server_name not in _connection_status:
        _connection_status[server_name] = {}
    
    _connection_status[server_name].update({
        'connected': connected,
        'last_updated': asyncio.get_event_loop().time(),
        'info': info or {}
    })
    
    _save_connection_status()
    logger.debug(f"Updated connection status for {server_name}: {connected}")

def get_connection_status(server_name: str) -> bool:
    """Get connection status for a server"""
    return _connection_status.get(server_name, {}).get('connected', False)

def get_all_connection_status() -> Dict[str, Dict[str, Any]]:
    """Get all connection statuses"""
    return _connection_status.copy()

def cleanup_stale_connections():
    """Clean up stale connection statuses"""
    global _connection_status
    current_time = asyncio.get_event_loop().time()
    stale_servers = []
    
    for server_name, status in _connection_status.items():
        last_updated = status.get('last_updated', 0)
        # Consider connections stale after 1 hour of no updates
        if current_time - last_updated > 3600:
            stale_servers.append(server_name)
    
    for server_name in stale_servers:
        _connection_status[server_name]['connected'] = False
        logger.debug(f"Marked stale connection as disconnected: {server_name}")
    
    if stale_servers:
        _save_connection_status()

def clear_connection_status(server_name: Optional[str] = None):
    """Clear connection status for a specific server or all servers"""
    global _connection_status
    
    if server_name:
        _connection_status.pop(server_name, None)
    else:
        _connection_status.clear()
    
    _save_connection_status()

# Initialize connection status on module load
_load_connection_status() 