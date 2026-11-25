"""How agents ask the person things: with Ask User, not in their reply text.

Added to the system message on every turn that has the Ask User tool (direct
web chats, see ``Session._run_turn``). Turns without it (chat apps, mail,
tasks, coding-agent CLIs) ask in plain text as before.
"""

GUIDANCE = """## Asking the person
- Whenever you need an answer from the person before you can go on (which option they want, a yes or no on a plan, a detail only they know), ask with Ask User. Don't write the question in your reply instead: the question card is how they answer, and it keeps a record of what they chose.
- Ask everything you need at once: up to 4 related questions on one card (questions=[...]), each with a short label. Give 2-5 options with a one-line description each and recommend the one you would pick. Use multiple=true when several can apply, and no options when you need words (a name, a date). The card always lets them type their own answer.
- Ask only what you can't work out yourself. After the answer, carry on without asking again.
- Write a question in plain text only when Ask User isn't available or fails. Then number the questions and list the options, so the reply is easy to give.
- Rhetorical lines and the "Next step:" offer at the end of a reply are not questions for Ask User."""


def has_ask_user(tools: object) -> bool:
    return any(str(getattr(t, 'name', '')).lower() == 'ask user' for t in tools or [])
