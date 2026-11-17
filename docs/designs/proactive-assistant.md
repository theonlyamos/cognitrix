# Proactive main assistant

The person's main assistant notices what needs them and says so at the right
time, without being asked, and without spending tokens or interrupting when
nothing changed. It builds on standing check-ins (`standing-assistant.md`):
check-ins stay read-only, and anything that changes the world is offered, not
done. Code: `cognitrix/assistant/`.

## Turning it on

- First sign-in: the setup dialog has a "Check in on my day" box (on by
  default). `onboarding.create_default_assistant` calls
  `assistant/setup.enable_defaults`: the new assistant becomes the main one and
  checks in every 4 hours on a starter brief (replies owed, meetings today,
  promises coming due, failing tasks, next-day deadlines; never send, buy or
  delete on its own).
- Existing people: a banner on the Dashboard and in Settings › Assistant
  (`POST /assistant/proactivity/enable`). A brief the person wrote is kept.

## How proactive, and when it may interrupt (Settings › Assistant)

`ProactivitySettings`, one row per person (`assistant/proactivity.py`):

| Setting | Default | What it does |
| --- | --- | --- |
| Level | Balanced | Off (nothing on its own), Brief only, Balanced, Eager (higher alert limit) |
| Morning brief | 08:00 | When the brief is written, in the person's time zone |
| Quiet hours | 21:00 to 08:00 | No pushes; alerts wait in the Inbox |
| Alerts a day | 2 | Pushes per day; urgent ones (deadline in hours, blocking work) skip it |
| Daily token limit | 200k | For work nobody asked for (check-ins, brief, prep, dream mode); 0 = none |

The time zone comes from the browser (`X-Timezone`), saved on every settings
change and Inbox load. Nothing proactive runs for someone who hasn't opened the
app for 7 days.

`should_interrupt` decides each proactive alert or offer. When it says no, the
row is saved with `quiet=True`: listed in the Inbox, never pushed, never sent
to chat apps, no toast. Counters per day live in `ProactivityDay` (pushes,
held, tokens, runs, skipped) and feed the "This week" panel.

## Spending nothing when nothing changed

Before a scheduled check-in starts, `checkins.gate_checkin` builds a
fingerprint of everything the app can see without a model (the situation
below, new mail, the brief text). Same fingerprint as last time: the run is
skipped and counted. It also stops at level Off or Brief only, after a week
away, when the daily token limit is used, and while backing off.

Backoff: when 5 of the last 6 answers to brief lines or offers were "not
useful" or dismissed, check-ins run 2x (then 4x) less often. A "useful" answer
halves it again. "Never show this" mutes the topic for good; check-ins and the
brief read the muted list and recent answers (`feedback_note`).

Routine check-ins and briefs run on the provider's small model
(`FAST_MODELS`); their tokens are counted against the daily limit.

## What it knows: the situation

`assistant/signals.situation` gathers, without a model: calls from the
connected calendar in the next day, approvals and questions waiting (stalled
after 2 hours), open offers (stalled after 3 days), tasks failing twice in a
row or stuck over 2 hours, memories with an end date in the next 3 days,
follow-ups due, and unread alerts.

- Check-ins get it in their run context, with new mail and the person's
  feedback.
- The main assistant's chats get a short "What's going on" note after the
  memory block (`awareness_block`, at most 1200 characters, cached per chat and
  refreshed only when it changed and 15 minutes passed, so the prompt cache
  holds).
- Blocking stuck items raise one alert per item per day.

## Morning brief

At the brief time (or "Brief me now" on the Dashboard) the home task starts a
run with trigger `brief`. The run reads calendar, mail, tasks and what's coming
due, then calls **Publish Brief** once with a summary and up to 8 lines in
sections (today, reply, waiting, stalled, offers, other). The Dashboard shows
it with Useful / Not useful / Never show this on each line, plus "Coming up"
from the situation. One brief per local day; a second publish replaces it.

## Meeting prep

30 to 60 minutes before a one-off call with a meeting link, a `prep` run
gathers context on the attendees and topic (mail threads, notes, memory) and
alerts the person with a short prep note. Recurring meetings are skipped.

## Mail, sorted by rules first

`assistant/mail.check_new_mail` reads new Gmail through the connected app's
cursor and sorts each message without a model: calendar invites, automated
senders and receipts (skipped), people the person knows from memory, replies
on their threads and direct questions (reply), the rest (FYI). Only the sorted
list reaches the check-in, marked as untrusted text.

## Follow Up Later

A chat tool: "come back to this chat in N minutes (5 minutes to 30 days) and
check X". When due, the agent gets a turn in that chat with the note; if
nothing changed it answers "NOTHING NEW" and the person isn't alerted. A busy
chat is retried every 30 seconds, and after 6 hours the person gets an alert
instead. Not available in task runs or incognito chats.

## Small things

- The default assistant prompt asks for at most one "Next step: …" line at the
  end of a reply; the chat shows it as a one-tap chip.
- Commitments ("I'll send it Friday") are saved as memories with an end date,
  so they show up as coming due.
- Requests the person repeats 3+ times in two weeks are noticed in check-ins,
  which can offer to make them an automation.

## Routes

`GET/PUT /assistant/proactivity`, `POST /assistant/proactivity/enable`,
`GET/POST /assistant/brief`, `POST /assistant/briefs/{id}/feedback`,
`GET /assistant/situation`, `GET /assistant/followups`,
`DELETE /assistant/followups/{id}`.

## Not built yet

People and project cards, chat summaries, folder-scoped memory and shared team
memory from the research doc.
