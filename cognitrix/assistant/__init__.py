"""The proactive side of a person's main assistant.

* ``proactivity``: the person's setting (Off, Brief only, Balanced, Eager),
  quiet hours, the daily interruption limit and the daily token limit for
  work nobody asked for.
* ``signals``: what is going on right now, worked out without a model:
  next meetings, waiting approvals and offers, stuck work, things coming due.
  It feeds the "what's going on" note in chats, the free change check before
  check-ins, and stuck-work alerts.
* ``brief``: the morning brief.
* ``followups``: Follow Up Later in chats.

See docs/designs/proactive-assistant.md.
"""
