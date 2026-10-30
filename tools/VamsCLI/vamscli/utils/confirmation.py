"""Second confirmation for destructive commands that already require ``--confirm``.

A destructive command refuses to run without its ``--confirm`` flag and, in text mode, then asks
"Are you sure you want to proceed?". That question can only be answered by a person at a terminal.
With no terminal on stdin (a script, a pipe, a CI job, a connector) ``click.confirm`` reads end of
input and aborts, so ``--confirm`` alone could never complete the operation outside ``--json-output``.

``confirm_destructive_action`` asks the question only when stdin is an interactive terminal; otherwise
the already-required ``--confirm`` flag is the confirmation. Callers check ``--confirm`` (and skip the
question in ``--json-output`` mode) before calling it.
"""

import sys

import click

DEFAULT_QUESTION = "Are you sure you want to proceed?"


def stdin_is_interactive() -> bool:
    """Return True when stdin is an interactive terminal a person can answer a prompt on."""
    stream = sys.stdin
    if stream is None:
        return False
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError, OSError):
        # No isatty, or a closed/detached stream: nobody can answer a prompt on it.
        return False


def confirm_destructive_action(question: str = DEFAULT_QUESTION) -> bool:
    """Ask the extra question of a destructive command whose ``--confirm`` flag was given.

    Returns the answer when stdin is an interactive terminal (Ctrl-C raises ``click.Abort``, as
    ``click.confirm`` does). Returns True without prompting when it is not, because ``--confirm``
    has already confirmed the operation and no one could answer the prompt.
    """
    if not stdin_is_interactive():
        return True
    return click.confirm(question)
