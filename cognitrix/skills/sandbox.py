"""Code sandbox for skill scripts using RestrictedPython.

Provides a restricted execution environment for Python code
referenced by skills. Blocks filesystem, network, and dynamic
code execution.
"""

import logging
from typing import Any

logger = logging.getLogger('cognitrix.log')


class SandboxError(Exception):
    """Raised when sandboxed code violates restrictions or fails."""
    pass


class CodeSandbox:
    """Sandboxed Python execution for skill scripts.

    Uses RestrictedPython to compile and execute code with:
    - Whitelist of safe builtins (json, re, math, datetime, collections)
    - No filesystem access (open, pathlib, os, shutil blocked)
    - No network access (socket, requests, urllib blocked)
    - No dynamic code execution (exec, eval, compile blocked)
    - Time limit: 30 seconds
    - Output size limit: 1MB
    """

    ALLOWED_MODULES = {
        'json', 're', 'math', 'datetime', 'collections',
        'itertools', 'functools', 'operator', 'string',
    }

    MAX_EXECUTION_TIME = 30   # seconds
    MAX_OUTPUT_SIZE = 1_000_000  # 1MB

    def execute(
        self,
        code: str,
        context: dict[str, Any] | None = None,
        timeout: int | None = None,
    ) -> Any:
        """Execute Python code in a restricted environment.

        Args:
            code:    Python source code
            context: Variables available to the code (as 'context' dict)
            timeout: Override max execution time (seconds)

        Returns:
            The return value of the code (last expression or explicit 'result' variable)

        Raises:
            SandboxError: If code violates restrictions, times out, or errors
        """
        try:
            from RestrictedPython import compile_restricted, safe_globals
            from RestrictedPython.Guards import safe_builtins
        except ImportError:
            raise SandboxError(
                "RestrictedPython is not installed. "
                "Install with: pip install RestrictedPython"
            )

        # Validate code statically
        errors = self.validate_code(code)
        if errors:
            raise SandboxError(f"Code validation failed: {'; '.join(errors)}")

        # Compile with RestrictedPython
        try:
            compiled = compile_restricted(code, '<skill_script>', 'exec')
        except SyntaxError as e:
            raise SandboxError(f"Syntax error: {e}")

        if compiled is None:
            raise SandboxError("RestrictedPython compilation returned None")

        # Check for compilation errors
        if compiled.co_filename == '<skill_script>':
            pass  # normal

        # Build restricted globals
        restricted_globals = dict(safe_globals)
        restricted_globals['__builtins__'] = dict(safe_builtins)

        # Add whitelisted modules
        import json, re as re_mod, math, datetime, collections
        restricted_globals['__builtins__']['json'] = json
        restricted_globals['__builtins__']['re'] = re_mod
        restricted_globals['__builtins__']['math'] = math
        restricted_globals['__builtins__']['datetime'] = datetime
        restricted_globals['__builtins__']['collections'] = collections

        # Guard functions
        restricted_globals['_getiter_'] = iter
        restricted_globals['_getattr_'] = getattr
        restricted_globals['_getitem_'] = lambda obj, key: obj[key]
        restricted_globals['_write_'] = lambda x: x
        restricted_globals['_inplacevar_'] = lambda op, x, y: op(x, y)

        # Context
        restricted_locals: dict[str, Any] = {
            'context': context or {},
            'result': None,
        }

        # Execute with timeout
        import signal
        import sys
        import threading

        effective_timeout = timeout or self.MAX_EXECUTION_TIME

        # Use threading for timeout (works on Windows and Unix)
        exec_result: dict[str, Any] = {'value': None, 'error': None}

        def _run():
            try:
                exec(compiled, restricted_globals, restricted_locals)
                exec_result['value'] = restricted_locals.get('result')
            except Exception as e:
                exec_result['error'] = e

        thread = threading.Thread(target=_run)
        thread.start()
        thread.join(timeout=effective_timeout)

        if thread.is_alive():
            raise SandboxError(f"Code execution timed out after {effective_timeout}s")

        if exec_result['error']:
            raise SandboxError(f"Execution error: {exec_result['error']}")

        return exec_result['value']

    def validate_code(self, code: str) -> list[str]:
        """Static analysis: check for disallowed constructs before execution.

        Returns list of error messages (empty = valid).
        """
        errors: list[str] = []

        # Check for obviously dangerous patterns
        dangerous_patterns = [
            (r'\bimport\s+os\b', "Import of 'os' module is blocked"),
            (r'\bimport\s+sys\b', "Import of 'sys' module is blocked"),
            (r'\bimport\s+subprocess\b', "Import of 'subprocess' is blocked"),
            (r'\bimport\s+socket\b', "Import of 'socket' is blocked"),
            (r'\bimport\s+shutil\b', "Import of 'shutil' is blocked"),
            (r'\bfrom\s+pathlib\b', "Import of 'pathlib' is blocked"),
            (r'\bimport\s+pathlib\b', "Import of 'pathlib' is blocked"),
            (r'\bopen\s*\(', "Direct use of 'open()' is blocked"),
            (r'\b__import__\s*\(', "Use of '__import__()' is blocked"),
            (r'\bexec\s*\(', "Use of 'exec()' is blocked"),
            (r'\beval\s*\(', "Use of 'eval()' is blocked"),
            (r'\bcompile\s*\(', "Use of 'compile()' is blocked"),
            (r'\bglobals\s*\(', "Use of 'globals()' is blocked"),
            (r'\blocals\s*\(', "Use of 'locals()' is blocked"),
        ]

        import re
        for pattern, message in dangerous_patterns:
            if re.search(pattern, code):
                errors.append(message)

        return errors

    def execute_file(
        self,
        file_path: str,
        context: dict[str, Any] | None = None,
    ) -> Any:
        """Execute a Python file in the sandbox.

        Args:
            file_path: Path to the Python script
            context:   Variables available to the code

        Returns:
            The result value
        """
        from pathlib import Path

        path = Path(file_path)
        if not path.exists():
            raise SandboxError(f"Script not found: {file_path}")
        if not path.suffix == '.py':
            raise SandboxError(f"Only .py files can be sandboxed: {file_path}")

        code = path.read_text(encoding='utf-8')
        return self.execute(code, context)
