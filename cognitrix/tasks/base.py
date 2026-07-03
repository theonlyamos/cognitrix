import logging
from datetime import datetime
from enum import Enum
from typing import Any, TypeAlias

from odbms import Model
from pydantic import Field, validator

from cognitrix.agents.base import Agent
from cognitrix.agents.evaluator import Evaluator
from cognitrix.sessions.base import Session

logger = logging.getLogger('cognitrix.log')

TaskList: TypeAlias = list['Task']

class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"

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

    async def team(self):
        agents: list[Agent] = []
        for agent_id in self.assigned_agents:
            agent = await Agent.get(agent_id)
            if agent:
                agents.append(agent)
        return agents

    async def sessions(self):
        return await Session.get_by_task_id(self.id)

    async def start(self):
        if len(self.assigned_agents):
            team = await self.team()

            if len(team):
                agent = team[0]

                if len(team) > 1:
                    agent.sub_agents = team[1:]

                evaluator = Evaluator(llm=agent.llm)
                agent.sub_agents.append(evaluator)

                self.status = TaskStatus.IN_PROGRESS
                await self.save()

                try:
                    session = Session(task_id=self.id, agent_id=agent.id)
                    session.started_at = (datetime.now()).strftime("%a %b %d %Y %H:%M:%S")
                    await session.save()

                    session.update_history({'role': 'system', 'type': 'text', 'content': self.description + '\n\nComplete the task step below:\n'})

                    print('[!]Starting task...\n')

                    steps = self.step_instructions.copy()

                    for key, value in steps.items():
                        if self.status == TaskStatus.IN_PROGRESS:
                            prompt = f'Step #{int(key) + 1}: '+ value['step']

                            await session(prompt, agent, stream=True)

                            # The agent's answer is the last assistant message with
                            # content; chat[-1] is a trailing turn-timing record.
                            answer = next(
                                (m.get('content', '') for m in reversed(session.chat)
                                 if m.get('role') == 'assistant' and m.get('content')),
                                '',
                            )
                            eval_prompt = "Task: "+value['step']
                            eval_prompt += "\n\nAgent Response:\n"+answer

                            await session(eval_prompt, evaluator, stream=True)
                            self.step_instructions[key]['done'] = True

                            await self.save()

                    session.completed_at = (datetime.now()).strftime("%a %b %d %Y %H:%M:%S")
                    await session.save()

                    self.status = TaskStatus.COMPLETED
                    await self.save()
                except Exception:
                    # Both dispatch paths land here: the in-process autostart path
                    # (no Celery postrun) and, redundantly, the Celery path. Mark
                    # FAILED so a crashed run doesn't linger as in_progress.
                    logger.exception("Task %s failed during execution", self.id)
                    self.status = TaskStatus.FAILED
                    await self.save()
                    raise

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

    class Config:
        json_encoders = {
            TaskStatus: lambda v: v.value
        }
