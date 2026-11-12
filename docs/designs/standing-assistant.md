# Standing assistant, offers, skill review and two new rule levels

Five features that make an agent behave more like a teammate than a chat
window. They share one idea: the agent may notice and prepare on its own, but
a person says yes before anything changes.

## Check-ins on a standing brief

An agent's **Check-ins** tab holds a standing brief with four parts: what
should stay true, where to look, what the person wants to hear about, and what
the agent must not do yet. Turning it on makes the agent check in on an
interval (15 minutes at the least, 4 hours by default).

- Settings live in `Agent.automation['standing']`. Only the routes in
  `api/routes/assistant.py` write them; the ordinary agent save keeps whatever
  is stored.
- Each standing agent has one "home" task (`Task.standing_agent_id`), which
  carries the schedule, the notes and the run history. It has a single authored
  step, so a check-in makes no planning call.
- The brief is restated at the start of every check-in
  (`tasks/standing.py: run_context`), so it survives context compaction.
- A check-in is read-only. While `permissions.read_only_run` is set, only
  tools known to look are allowed (`READ_ONLY_TOOLS`, plus read-only shell
  commands, GET requests and connected-app actions that only read). Everything
  else is refused, including edits that would normally run without asking.
  Notes, alerts and offers still work.
- One agent per person can be the main assistant. The dashboard's ask bar and
  chat apps start with it.

## Offers

During a check-in the agent can call **Offer Help** (title, why, plan). The
offer is stored as an `AgentOffer` and shown in the Inbox with Accept and
Dismiss.

- Accept starts an ordinary chat with that agent, opened with the plan it
  wrote and the brief's "must not do yet" limits. The chat follows the agent's normal rules, so risky steps still ask.
- Dismiss is remembered: the same title is refused later, and the most recent
  dismissals are listed in the next check-in's context.
- Limits: 3 offers per check-in, and a cap on unanswered offers.

## Skills with review

A skill is never saved without a person reading it first.

- **Save as skill** in a chat's menu asks the agent's model to write a draft
  from the transcript (`skills/drafts.py: draft_from_chat`).
- Agents get a **Propose Skill** tool in signed-in chats for the same purpose.
- Drafts (`SkillDraft`) wait in the Inbox. The person can edit name,
  description and instructions, then approve or reject. Approving writes the
  skill with `SkillManager.create_skill`. Skills are shared by everyone on the
  server, so only an admin can approve.

## Stop all

`POST /agents/{id}/stop-all` stops the agent's running chats, cancels its task
runs and clears pending check-ins, for the signed-in person only. The button
is on the agent's Overview tab.

## Two more rule levels

Per tool, next to Default, Allow, Ask first and Never:

- **Only when I ask** (`told`): runs in a chat the person is in. When the agent
  works on its own (a schedule, a check-in, a chat another agent started) it
  asks first.
- **Hand off to me** (`handoff`): never runs. What the agent prepared is put in
  the Inbox for the person to do themselves, with saved secrets hidden.

Both are decided in `safety/permissions.py: decide_tool_call`, which now takes
the interface the turn runs on.

## Not covered

- Check-ins run on the task queue, so they need the worker running, like any
  scheduled task.
- A check-in cannot use tools that need a visible screen or a person's
  approval; it can only prepare and offer.
- Skills remain server-wide; there are no per-person skills yet.
