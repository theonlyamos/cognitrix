"""CLI argument parsing.

--prompt-file lets long/multi-line/special-character prompts bypass the shell
(passing them on the command line mangled backticks/quotes and broke the
Windows console-script shim). It must parse cleanly alongside --prompt/-p and
--prompt-template.
"""

import sys

from cognitrix.cli.args import get_arguments


def _parse(argv):
    sys.argv = ["cognitrix", *argv]
    return get_arguments()


def test_prompt_file_parses():
    a = _parse(["--prompt-file", "task.txt"])
    assert a.prompt_file == "task.txt"
    assert a.prompt == ""


def test_prompt_flags_are_unambiguous():
    # The three prompt-* flags must not collide in argparse.
    assert _parse(["-p", "hi"]).prompt == "hi"
    assert _parse(["--prompt", "hi"]).prompt == "hi"
    assert _parse(["--prompt-file", "f.txt"]).prompt_file == "f.txt"
    assert _parse(["--prompt-template", "t.txt"]).prompt_template == "t.txt"


def test_prompt_file_default_empty():
    assert _parse(["-p", "x"]).prompt_file == ""
