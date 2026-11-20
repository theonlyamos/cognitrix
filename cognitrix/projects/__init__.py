"""Projects: a container that chats and tasks belong to.

A project holds what every chat in it starts from: instructions, the agents
that work in it, working folders, knowledge collections, its own memories and
the computer its agents use (the sandbox or one of the person's paired PCs).

- ``models.py``  the Project table
- ``store.py``   reading, writing and the API view
- ``context.py`` the project of the running turn (a ContextVar) and what the
  turn takes from it: the prompt note, folders, knowledge and sandbox identity

Projects are not listed in the sidebar; the Projects page manages them.
See docs/designs/projects.md.
"""
