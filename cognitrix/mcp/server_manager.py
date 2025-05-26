import json
import logging
import asyncio
import time
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Any, Union
from dataclasses import dataclass, asdict
from pydantic import BaseModel, Field

from cognitrix.config import COGNITRIX_HOME, MCP_CONFIG_FILE, ensure_cognitrix_home

logger = logging.getLogger('cognitrix.log')

class MCPTransportType(str, Enum):
    STDIO = "stdio"
    HTTP = "http"
    SSE = "sse"

@dataclass
class MCPServerConfig:
    """Configuration for an MCP server"""
    name: str
    transport: MCPTransportType
    description: str = ""
    
    # Common fields
    url: Optional[str] = None
    
    # STDIO specific fields
    command: Optional[str] = None
    args: Optional[List[str]] = None
    env: Optional[Dict[str, str]] = None
    working_directory: Optional[str] = None
    
    # HTTP/SSE specific fields
    headers: Optional[Dict[str, str]] = None
    timeout: int = 30
    
    # Server status
    enabled: bool = True
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'MCPServerConfig':
        """Create from dictionary"""
        # Ensure transport is an enum
        if 'transport' in data and isinstance(data['transport'], str):
            data['transport'] = MCPTransportType(data['transport'])
        return cls(**data)
    
    def validate(self) -> List[str]:
        """Validate server configuration and return list of errors"""
        errors = []
        
        if not self.name:
            errors.append("Server name is required")
        
        if self.transport == MCPTransportType.STDIO:
            if not self.command:
                errors.append("Command is required for STDIO transport")
        elif self.transport in [MCPTransportType.HTTP, MCPTransportType.SSE]:
            if not self.url:
                errors.append("URL is required for HTTP/SSE transport")
        
        return errors

class MCPServerManager:
    """Manages MCP server configurations and connections"""
    
    def __init__(self):
        self.config_file = MCP_CONFIG_FILE
        self.servers: Dict[str, MCPServerConfig] = {}
        self._load_configuration()
    
    def _load_configuration(self):
        """Load server configurations from file"""
        ensure_cognitrix_home()
        
        if self.config_file.exists():
            try:
                with open(self.config_file, 'r') as f:
                    data = json.load(f)
                
                # Load servers
                for server_data in data.get('servers', []):
                    try:
                        server_config = MCPServerConfig.from_dict(server_data)
                        self.servers[server_config.name] = server_config
                    except Exception as e:
                        logger.error(f"Error loading server config: {e}")
                        
                logger.info(f"Loaded {len(self.servers)} MCP server configurations")
                
            except Exception as e:
                logger.error(f"Error reading MCP config file: {e}")
        else:
            # Create default configuration file
            self._save_configuration()
    
    def _save_configuration(self):
        """Save current server configurations to file"""
        ensure_cognitrix_home()
        
        data = {
            'servers': [server.to_dict() for server in self.servers.values()],
            'version': '1.0'
        }
        
        try:
            with open(self.config_file, 'w') as f:
                json.dump(data, f, indent=2)
            logger.info(f"Saved MCP configuration to {self.config_file}")
        except Exception as e:
            logger.error(f"Error saving MCP config file: {e}")
            raise
    
    def add_server(self, server_config: MCPServerConfig) -> bool:
        """Add a new server configuration"""
        errors = server_config.validate()
        if errors:
            logger.error(f"Server validation failed: {', '.join(errors)}")
            return False
        
        if server_config.name in self.servers:
            logger.warning(f"Server '{server_config.name}' already exists, updating...")
        
        self.servers[server_config.name] = server_config
        self._save_configuration()
        logger.info(f"Added MCP server: {server_config.name}")
        return True
    
    def remove_server(self, name: str) -> bool:
        """Remove a server configuration"""
        if name in self.servers:
            del self.servers[name]
            self._save_configuration()
            logger.info(f"Removed MCP server: {name}")
            return True
        return False
    
    def get_server(self, name: str) -> Optional[MCPServerConfig]:
        """Get server configuration by name"""
        return self.servers.get(name)
    
    def list_servers(self, enabled_only: bool = False) -> List[MCPServerConfig]:
        """List all server configurations"""
        servers = list(self.servers.values())
        if enabled_only:
            servers = [s for s in servers if s.enabled]
        return servers
    
    def enable_server(self, name: str) -> bool:
        """Enable a server"""
        if name in self.servers:
            self.servers[name].enabled = True
            self._save_configuration()
            return True
        return False
    
    def disable_server(self, name: str) -> bool:
        """Disable a server"""
        if name in self.servers:
            self.servers[name].enabled = False
            self._save_configuration()
            return True
        return False
    
    def update_server(self, name: str, **kwargs) -> bool:
        """Update server configuration"""
        if name not in self.servers:
            return False
        
        server = self.servers[name]
        for key, value in kwargs.items():
            if hasattr(server, key):
                setattr(server, key, value)
        
        errors = server.validate()
        if errors:
            logger.error(f"Server validation failed: {', '.join(errors)}")
            return False
        
        self._save_configuration()
        logger.info(f"Updated MCP server: {name}")
        return True

    def export_config(self, output_file: Optional[Path] = None) -> Path:
        """Export configuration to a file"""
        if output_file is None:
            output_file = Path(f"mcp_config_export_{int(time.time())}.json")
        
        data = {
            'servers': [server.to_dict() for server in self.servers.values()],
            'exported_at': time.time(),
            'version': '1.0'
        }
        
        with open(output_file, 'w') as f:
            json.dump(data, f, indent=2)
        
        logger.info(f"Exported MCP configuration to {output_file}")
        return output_file
    
    def import_config(self, input_file: Path, merge: bool = True) -> bool:
        """Import configuration from a file"""
        try:
            with open(input_file, 'r') as f:
                data = json.load(f)
            
            if not merge:
                self.servers.clear()
            
            for server_data in data.get('servers', []):
                try:
                    server_config = MCPServerConfig.from_dict(server_data)
                    self.servers[server_config.name] = server_config
                except Exception as e:
                    logger.error(f"Error importing server config: {e}")
            
            self._save_configuration()
            logger.info(f"Imported MCP configuration from {input_file}")
            return True
            
        except Exception as e:
            logger.error(f"Error importing config file: {e}")
            return False

# Global instance
mcp_server_manager = MCPServerManager() 