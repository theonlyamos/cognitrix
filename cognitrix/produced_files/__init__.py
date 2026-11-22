"""Files agents make: shown in the chat with a preview.

Two ways a file gets here:

* The agent shares it on purpose with the Share File tool
  (``cognitrix/tools/share_file.py``). The file is copied into Cognitrix and
  shows as a big preview card under that step of the reply.
* At the end of a turn that used tools, Cognitrix looks for files the turn
  made where it worked (this machine's chat folders, the sandbox, or a paired
  PC). Those show on a small "N more files made" shelf under the reply. When
  the agent shared nothing, the few that look like results (videos, images,
  audio, documents) are shown as cards instead.

``models.ProducedFile`` is one such file; ``service`` holds the logic,
``sources`` reads each location, ``previews`` makes thumbnails and the
details the viewer shows, and ``kinds`` says what a file is and which files
are noise.
"""
