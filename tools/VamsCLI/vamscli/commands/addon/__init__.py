"""Upstream-owned add-on lane. Upstream VAMS edits this file when it ships an add-on that adds
commands; a fork does not.

Top-level ``vamscli`` commands the optional add-ons add. ``main.py`` registers every Click command or
group in ``ADDON_COMMANDS`` on the root ``cli`` group after the core commands and before
``commands/custom/CUSTOM_COMMANDS``, so it appears in ``vamscli --help`` beside them. Each add-on keeps
its command group in one module of this package, written like the core command files, gates its
commands on the add-on's feature switch (``@requires_feature``) and is listed here. A command whose
name is already registered is refused: ``vamscli`` fails at import with an error that names it, so an
add-on command never replaces a core one, and a custom command never replaces an add-on one.
"""

# Modules are imported whole, so `vamscli.commands.addon.<module>` keeps naming the module rather than
# its command group.
from . import edh

ADDON_COMMANDS = [edh.edh]
