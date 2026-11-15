# Agent swarms

A swarm is a group of workers that one agent (the lead) starts for one job.
The workers run at the same time, each in its own context with its own brief,
and the lead gets one combined report instead of one message per worker.
Code: `cognitrix/swarms/` (engine), `cognitrix/tools/swarm.py` (agent tools),
`cognitrix/api/routes/swarms.py` (the chat page).

## What the lead does
Two tools are added to a chat's agent for each turn (web chats of a signed-in
person; not incognito, group or API-key chats, and not agents with
"can create sub agents" turned off):

* **Spawn Workers** starts a swarm. Either `workers` (a list of
  `{brief, name, role, model, edit, depends_on}`), or `items` plus a `brief`
  with `{item}` in it (one worker per item), or `attempts` plus a `brief`
  (best of N). `review` turns the quality gate on, `wait` says when to report:
  `all` (default), `any`, a number, or `failure`.
* **Swarm** looks in and steers: `status`, `wait`, `result`, `message`, `stop`,
  `merge`.

A worker's `role` is the name of a saved agent (it then runs as that agent,
including agents on installed coding CLIs) or a made-up role, which runs as a
copy of the lead told its role. `model` gives one worker another model
(`provider/model`, or a model name on the lead's provider).

Each worker is a background chat (`sessions/agent_chats.py`) marked
`started_by.swarm_id`. Worker chats do not show in the chat list; they open
from the Swarm card. A worker cannot start workers (depth 1).

## The report
When the swarm is due to report, the lead's chat gets one turn with every
finished worker's summary (the first lines of its reply, after "Summary:").
The full reply is saved to `~/.cognitrix/swarms/<swarm id>/<worker>/result.md`
with any files the worker left in its scratch folder, and the lead reads it
with Swarm (action `result`). The data folder is not a working folder, so
results are read through the tool, not through file access. If the lead is in
the middle of a turn the report is steered into it; if the lead calls Swarm
(action `wait`), the report is that call's result.

## The board
Every worker is a row on the swarm's board (`SwarmWorker`): queued, running,
reviewing, done, failed or stopped, with what it depends on. The engine, not
the models, decides what starts: a worker starts when a slot is free and
everything it depends on is done, and gets those workers' summaries in its
brief. A worker whose dependency failed fails too.

Workers get one tool, **Swarm Team**: `board`, `result` (a teammate's result),
`message` (a teammate or the lead) and `add_work` (put a job it found on the
board for another worker). Messages are capped at 6 per worker and 3 per pair,
and a worker is told not to reply just to acknowledge.

The lead sees a ledger line in each report (done, failed, running, waiting,
tokens). A running worker that produces nothing for 5 minutes
(`COGNITRIX_SWARM_STALL_SECONDS`) is flagged once and the lead is told.

## Quality gate and best of N
With `review` on, a finished worker's result is checked against its brief by
the person's Reviewer agent's model (the lead's model when there is none). A
result that fails is sent back once with what to fix. With `attempts`, the
same brief runs N times (optionally on different models) and a judge picks one;
the report names the pick and why.

## Workers that change files
Workers read the chat's working folders but cannot change files or run
commands unless their spec says `edit`. One editor works in the folders
directly. With several editors and a git repository as the working folder,
each gets its own worktree on its own branch
(`cognitrix-swarm/<swarm>/<worker>`), next to the repository, or inside it in
`.cognitrix-swarm/` (ignored through `.git/info/exclude`) when the server only
allows folders under the project. The worker's changes are committed when it
finishes, and the lead merges with Swarm (action `merge`), which refuses when
the folder has uncommitted changes and backs out on a conflict. Without git,
editors take turns.

## Limits
Per person, in Settings > Swarms (`SwarmSettings`): on/off, workers at once
(4), workers per swarm (25), how many may start without asking (5) and a token
limit per swarm (2,000,000). Above the "without asking" number the lead asks
the person with a question card, unless the message was sent with the Swarm
switch on. An admin can cap a person's workers at once and per swarm in
Settings > People. Server ceilings: `COGNITRIX_SWARM_MAX_CONCURRENCY` (8),
`COGNITRIX_SWARM_MAX_WORKERS` (50), `COGNITRIX_SWARM_MAX_TOKENS` (20,000,000).
A worker's turn is limited by `COGNITRIX_SWARM_WORKER_TIMEOUT` (900 seconds).
A worker that hits a provider's rate limit is retried with a growing delay.

## In the app
* The composer's + menu has a **Swarm** switch: the agent is told to split the
  job, and may start workers without asking.
* A **Swarm card** in the chat and a **Swarm** section in the chat's right
  sidebar show progress and one row per worker (what it is doing, its summary,
  Open its chat, Stop, Merge), with Stop all.
* Stop all on an agent's page stops its swarms too.

## Team group chats
A message to the whole team (`@all`, `@team`, `@everyone`) is answered by the
members at the same time, each seeing the chat as it was. Their answers are
shown whole, one after another, and the team lead speaks last with all of them
in front of it (`teams/group_chat.py`).

## Swarm tasks
A task with **Run as a swarm** (`Task.swarm`, also on Create Task and Update
Task) gives its step agents Spawn Workers. Inside a task run the workers are
copies of the step's agent, the call waits for all of them and returns the
whole report, and their tokens count for the run. The run is an ordinary
durable task run, so it survives restarts and can be scheduled. When a step is
run again after a restart or a retry, workers that already finished keep their
results.

Chat swarms that were running when the engine stopped are picked up at
startup (`service.recover`).

## Measuring it
`cognitrix swarm-evals <email> --agent Assistant --jobs 4 --baseline` runs a
set of 20 jobs (`cognitrix/swarms/evals.py`: lists, multi-part jobs, best-of
jobs and small jobs that should not use workers) with and without workers, and
a judge model scores each answer. It makes real model calls.
