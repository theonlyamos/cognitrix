import logging
from enum import Enum
from typing import Any, TypeAlias

from odbms import Model
from pydantic import Field, validator

from cognitrix.agents.base import Agent
from cognitrix.sessions.base import Session

logger = logging.getLogger('cognitrix.log')

TaskList: TypeAlias = list['Task']

class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

class Task(Model):
    """
    Initializes the Task object by assigning values to its attributes.

    Args:
        description (str): The task to perform or query to answer.
        args (tuple): The positional arguments to be passed to the function.
        kwargs (dict): The keyword arguments to be passed to the function.

    Returns:
        None
    """

    title: str
    """The title of the task"""

    description: str
    """The task|query to perform|answer"""

    step_instructions: dict[str, dict[str, Any]] = {}
    """Line by line instructions for completing the task, keyed by step index ("0", "1", ...)"""

    done: bool = False
    """Checks/Sets whether the task has been completed"""

    autostart: bool = False
    """Automatically start the task when it is ready"""

    status: TaskStatus = Field(default=TaskStatus.PENDING)
    """Status of the task"""

    assigned_agents: list[str] = Field(default_factory=list)
    """List of ids of agents assigned to this task"""

    results: list[str] = Field(default_factory=list)
    """List of results from the task"""

    pid: str | None = None
    """Worker Id of task"""

    team_id: str | None = None
    """ID of the team assigned to this task"""

    callback_url: str | None = None
    """Webhook POSTed on run completion (API-started tasks only). Stripped
    from API projections — callback URLs routinely embed capability tokens."""

    callback_key_id: str | None = None
    """APIKey that registered the callback; its webhook_secret signs the
    payload. Revoked/expired keys stop deliveries."""

    schedule_at: str | None = None
    """One-shot schedule: naive-UTC 'YYYY-MM-DD HH:MM:SS'. At most one of
    schedule_at/schedule_interval/schedule_cron may be set."""

    schedule_interval: int | None = None
    """Recurring schedule: run every N seconds (minimum 60)."""

    schedule_cron: str | None = None
    """Recurring schedule: 5-field cron expression, evaluated in server-local time."""

    next_run_at: str | None = None
    """When the scheduler fires next (naive UTC). Single dispatch column for
    all schedule types; the scheduler claims a fire by compare-and-set on it."""

    schedule_enabled: bool = False
    """Pause/resume toggle for the schedule."""

    async def team(self):
        agents: list[Agent] = []
        for agent_id in self.assigned_agents:
            agent = await Agent.get(agent_id)
            if agent:
                agents.append(agent)
        return agents

    async def sessions(self):
        return await Session.get_by_task_id(self.id)

    async def start(self, resume: bool = False):
        """Execute this task via the orchestrator (plan → assign → execute →
        gate → synthesize). Returns the TaskRun, or None if the task was
        cancelled before pickup."""
        from cognitrix.tasks.orchestrator import run as run_orchestration
        return await run_orchestration(self, resume=resume, interface='web')

    @classmethod
    async def list_tasks(cls) -> TaskList:
        return cls.all()

    @classmethod
    async def delete(cls, task_id: str):
        """Delete task by id"""
        return cls.remove({'id': task_id})

    @classmethod
    async def assign_to_team(cls, task_id: str, team_id: str):
        """Assign a task to a team"""
        task = cls.get(task_id)
        if task:
            task.team_id = team_id
            task.save()
            return task
        return None

    @validator("status", pre=True)
    def parse_status(cls, value):
        if isinstance(value, TaskStatus):
            return value
        return TaskStatus(value)

    # A DB row may store these collections as NULL; coerce back to the empty
    # default so loading the model doesn't fail validation.
    @validator("step_instructions", pre=True)
    def _coerce_null_steps(cls, value):
        return {} if value is None else value

    @validator("assigned_agents", "results", pre=True)
    def _coerce_null_lists(cls, value):
        return [] if value is None else value

    @validator("schedule_enabled", pre=True)
    def _coerce_null_bool(cls, value):
        return False if value is None else value

    class Config:
        json_encoders = {
            TaskStatus: lambda v: v.value
        }
