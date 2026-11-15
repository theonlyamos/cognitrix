"""Agent payments: an agent pays at a checkout only after the person approves
that exact payment (merchant, site and total), within limits they set.

- ``models.py``: the person's settings and cards, and each payment request.
- ``service.py``: asking, approving, limits, and the card handed over once.
- ``checkout.py``: fills the card into the checkout page on the agent's
  computer and presses Pay, so the model never sees the card number.

Nothing here can approve a payment except the person, in the app: not the
agent, not "always allow", not the second-model reviewer, not a chat app
button, not a task's approval settings. See ``service.approve``.
"""
