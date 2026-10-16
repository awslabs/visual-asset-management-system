"""Fork extension point. Upstream VAMS ships this file empty and does not modify it, so edits here do
not conflict on upgrade.

Top-level ``vamscli`` commands a fork adds. ``main.py`` registers every Click command or group in
``CUSTOM_COMMANDS`` on the root ``cli`` group, so it appears in ``vamscli --help`` beside the core
commands. Put each command group in a module of this package, written like the core command files
(``commands/roleUserConstraints.py``: profile support, ``--json-output``, typed errors), and list it
here. Command names prefixed ``custom-`` are recommended so a later core command does not collide
with one added here.
"""

CUSTOM_COMMANDS = []
