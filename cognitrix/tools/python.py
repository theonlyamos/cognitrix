import functools
import logging
import multiprocessing
import sys
from io import StringIO

from pydantic import Field

from cognitrix.tools.base import Tool

NotImplementedErrorMessage = 'this tool does not suport async'

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
    datefmt='%d-%b-%y %H:%M:%S',
    level=logging.WARNING
)
logger = logging.getLogger('cognitrix.log')


@functools.cache
def warn_once() -> None:
    """Warn once about the dangers of PythonREPL."""
    logger.warning("Python REPL can execute arbitrary code. Use with caution.")


class PythonREPL(Tool):
    """Simulates a standalone Python REPL."""

    globals: dict | None = Field(default_factory=dict, alias="_globals")
    locals: dict | None = Field(default_factory=dict, alias="_locals")

    name: str = "Python"
    category: str = "system"
    description: str = """Use this tool to execute python code.

    :param code: the valid python code to execute
    """

    @classmethod
    def worker(
        cls,
        command: str,
        globals: dict | None,
        locals: dict | None,
        queue: multiprocessing.Queue,
    ) -> None:
        old_stdout = sys.stdout
        sys.stdout = mystdout = StringIO()
        try:
            exec(command, globals, locals)
            sys.stdout = old_stdout
            queue.put(mystdout.getvalue())
        except Exception as e:
            sys.stdout = old_stdout
            queue.put(repr(e))

    def run(self, code: str, timeout: int | None = None) -> str:
        """Run code with own globals/locals and returns anything printed.
        Timeout after the specified number of seconds."""

        # Warn against dangers of PythonREPL
        warn_once()

        queue: multiprocessing.Queue = multiprocessing.Queue()

        # Only use multiprocessing if we are enforcing a timeout
        if timeout is not None:
            # create a Process
            p = multiprocessing.Process(
                target=self.worker, args=(code, self.globals, self.locals, queue)
            )

            # start it
            p.start()

            # wait for the process to finish or kill it after timeout seconds
            p.join(timeout)

            if p.is_alive():
                p.terminate()
                return "Execution timed out"
        else:
            self.worker(code, self.globals, self.locals, queue)
        # get the result from the worker function
        return queue.get()
