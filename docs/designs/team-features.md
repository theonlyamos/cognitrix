# Team features: sharing, personal skills, channel mentions, Teams, WhatsApp, meeting notes

## Shared agents with roles
An agent's owner shares it on the agent's **Sharing** tab with named people or
everyone on the server, as **Can use** or **Can edit** (`Agent.shared_with`,
rules in `cognitrix/agents/access.py`).

* Can use: chat with it and give it tasks.
* Can edit: also change its prompt, model, tools and permissions.
* Only the owner can share, delete, set check-ins, or change what the agent
  reaches: connected apps, folders, knowledge, saved sign-ins, coding CLIs,
  automation and memory settings.

A shared agent runs on the owner's model key and connected apps.

## Per-person skills
Skills a person writes or approves are theirs (`cognitrix/skills/personal.py`,
Settings > Skills). Their agents load them before the shared ones. An admin can
publish one for everyone. Skill drafts are reviewed only by the person whose
agent wrote them.

## Slack channel mentions
Invite the Slack app to a channel and @mention it; it answers in a thread, and
each thread is its own conversation (`ChannelThread`). The linked person gets
their agent as usual. Other people are refused unless "Answer other people in
channels" is on, and then the turn is a guest turn
(`permissions.guest_run`): read-only, limited to reading the agent's folders
and knowledge and the web, in an incognito chat. No connected apps, memory,
past chats, saved sign-ins, shell or screen.

Slack apps created before this need the `app_mentions:read` scope and the
`app_mention` bot event, then a reinstall.

## Microsoft Teams and WhatsApp
Both call the engine, so they need a public HTTPS address (not the desktop app
alone).

* Teams (`channels/teams.py`): Azure Bot, single tenant. Calls arrive at
  `/api/v1/channels/teams/<id>/messages` and are checked against the Bot
  Framework signing keys (issuer, audience, serviceUrl claim, Teams
  endorsement) and the tenant. Direct chat plus @mentions in channels.
* WhatsApp (`channels/whatsapp.py`): Meta WhatsApp Business Cloud API. Calls
  arrive at `/api/v1/channels/whatsapp/<id>/webhook` and are checked against
  the app secret (`X-Hub-Signature-256`). Direct chat only. WhatsApp only
  delivers free-form messages within 24 hours of the person's last message,
  so later alerts stay in the Inbox. Messages can't be edited, so a button's
  outcome is sent as a reply.

## Meeting notes from recordings
Meetings page: upload audio or video (up to 1 GB, 4 hours). ffmpeg converts it
to 16 kHz mono WAV in 9 minute pieces, the person's Voice provider
transcribes them, and one of their agents' models writes a summary, decisions,
action items with owners, open questions and discussion
(`cognitrix/meetings/notes.py`). The recording is deleted after
transcription; the transcript and notes stay until deleted. Without ffmpeg
only WAV and small audio files work. The Docker image now includes ffmpeg.
