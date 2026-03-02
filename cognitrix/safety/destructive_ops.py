"""Detection and classification of potentially destructive operations."""

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class RiskLevel(Enum):
    """Risk levels for operations."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class RiskAssessment:
    """Assessment of operation risk."""
    risk_level: RiskLevel
    categories: list[str]
    details: str
    confidence: float = 1.0


# Operation risk categories
DESTRUCTIVE_CATEGORIES = {
    'file_deletion': {
        'tools': ['delete_path', 'remove_file', 'delete_file'],
        'keywords': [
            'delete', 'remove', 'rm ', 'del ', 'destroy', 'eliminate',
            'erase', 'wipe', 'purge', 'clean', 'clear'
        ],
        'risk_level': RiskLevel.HIGH,
        'description': 'File or directory deletion'
    },
    'file_modification': {
        'tools': ['update_file', 'write_file', 'overwrite_file'],
        'keywords': [
            'overwrite', 'replace', 'modify', 'edit', 'change',
            'update', 'rewrite', 'truncate'
        ],
        'risk_level': RiskLevel.MEDIUM,
        'description': 'File content modification'
    },
    'code_execution': {
        'tools': ['python_repl', 'terminal_command', 'execute_code', 'eval'],
        'keywords': [
            'exec', 'eval', 'execfile', 'compile', '__import__',
            'subprocess', 'os.system', 'os.popen', 'spawn',
            'rm -rf', 'format c:', 'dd if=', 'del /f /s',
            'shutdown', 'reboot', 'halt'
        ],
        'risk_level': RiskLevel.HIGH,
        'description': 'Arbitrary code execution'
    },
    'system_modification': {
        'tools': ['create_file', 'create_directory', 'mkdir'],
        'keywords': [
            'sudo', 'chmod', 'chown', 'chgrp', 'install',
            'pip install', 'npm install', 'apt-get', 'yum',
            'registry', 'system32', 'etc/', 'bin/'
        ],
        'risk_level': RiskLevel.MEDIUM,
        'description': 'System configuration modification'
    },
    'network_external': {
        'tools': ['internet_search', 'web_scraper', 'open_website'],
        'keywords': [
            'post', 'send', 'submit', 'upload', 'download',
            'curl', 'wget', 'fetch', 'request'
        ],
        'risk_level': RiskLevel.LOW,
        'description': 'External network communication'
    },
    'data_exposure': {
        'tools': [],
        'keywords': [
            'password', 'secret', 'key', 'token', 'credential',
            'api_key', 'private_key', '.env', 'config'
        ],
        'risk_level': RiskLevel.HIGH,
        'description': 'Potential sensitive data exposure'
    }
}


class DestructiveOpDetector:
    """Detects and classifies potentially destructive operations."""
    
    def __init__(self):
        self.categories = DESTRUCTIVE_CATEGORIES
    
    def analyze(self, tool_name: str, params: dict) -> RiskAssessment:
        """
        Analyze a tool call for risk.
        
        Args:
            tool_name: Name of the tool being called
            params: Tool parameters
            
        Returns:
            RiskAssessment with level and categories
        """
        tool_name_lower = tool_name.lower()
        params_str = str(params).lower()
        combined = f"{tool_name_lower} {params_str}"
        
        detected_categories = []
        max_risk = RiskLevel.LOW
        details = []
        
        for category_name, config in self.categories.items():
            detected = False
            
            # Check tool name match
            if any(t in tool_name_lower for t in config['tools']):
                detected = True
            
            # Check keyword match in params
            if any(kw in combined for kw in config['keywords']):
                detected = True
            
            if detected:
                detected_categories.append(category_name)
                
                # Update max risk
                if config['risk_level'].value > max_risk.value:
                    max_risk = config['risk_level']
                
                details.append(config['description'])
        
        # Build details string
        details_str = "; ".join(details) if details else "No specific risk detected"
        
        return RiskAssessment(
            risk_level=max_risk,
            categories=detected_categories,
            details=details_str
        )
    
    def is_destructive(self, tool_name: str, params: dict, threshold: RiskLevel = RiskLevel.MEDIUM) -> bool:
        """
        Quick check if operation is destructive above threshold.
        
        Args:
            tool_name: Tool name
            params: Tool parameters
            threshold: Minimum risk level to consider destructive
            
        Returns:
            True if operation is at or above threshold
        """
        assessment = self.analyze(tool_name, params)
        risk_values = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2}
        return risk_values[assessment.risk_level] >= risk_values[threshold]
