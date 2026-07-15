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


def test_model_is_unset_unless_explicitly_supplied():
    assert _parse(["--provider", "openrouter"]).model == ""
    assert _parse(["--provider", "openrouter", "--model", "custom/model"]).model == "custom/model"


def test_stream_flag_is_toggleable():
    # `--stream` used type=bool, so `--stream false` was truthy and streaming
    # could never be disabled. BooleanOptionalAction gives a real off-switch.
    assert _parse(["-p", "x"]).stream is True          # default on
    assert _parse(["--stream", "-p", "x"]).stream is True
    assert _parse(["--no-stream", "-p", "x"]).stream is False


def test_dangerously_skip_permissions_flag():
    # Off by default; the flag turns it on (main() wires it to the auto-approve
    # + sandbox-shell env vars the gate and bash tool read).
    assert _parse(["-p", "x"]).dangerously_skip_permissions is False
    assert _parse(["--dangerously-skip-permissions", "-p", "x"]).dangerously_skip_permissions is True
