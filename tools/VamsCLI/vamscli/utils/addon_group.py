"""The Click group class of an add-on's top-level command group (``commands/addon/``).

An add-on group is listed in ``vamscli --help`` only when the feature-switch snapshot of the profile
the invocation uses (the one ``vamscli auth login`` stored) carries the add-on's switch, so on a
deployment without the add-on the help does not name it. Reading the snapshot is a local file read;
listing the help makes no request. The group is still registered: ``vamscli <group> ...`` runs, and
each of its commands refuses on its own (``@requires_feature``) with the add-on's message.
"""

import sys
from typing import Optional, Sequence

import click

from .profile import ProfileManager, read_active_profile_name


def profile_named_on_command_line(argv: Sequence[str]) -> Optional[str]:
    """The value of the root ``--profile`` option, read off ``argv`` without parsing it.

    ``--help`` is processed before ``--profile`` (Click handles eager options first), so a help
    listing cannot read the profile from the context. Root options precede the command, so the scan
    stops at the first token that is not an option.
    """
    tokens = list(argv[1:])
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "--profile":
            return tokens[index + 1] if index + 1 < len(tokens) else None
        if token.startswith("--profile="):
            return token.split("=", 1)[1]
        if not token.startswith("-"):
            return None
        index += 1
    return None


def invocation_profile_name() -> str:
    """The profile this invocation uses: ``--profile`` when given, else the active profile."""
    ctx = click.get_current_context(silent=True)
    root = ctx.find_root() if ctx else None
    if root is not None and isinstance(root.obj, dict) and root.obj.get("profile_name"):
        return root.obj["profile_name"]
    return profile_named_on_command_line(sys.argv) or read_active_profile_name()


class AddonGroup(click.Group):
    """A group listed in its parent's help only while the profile's snapshot carries ``feature_switch``."""

    def __init__(self, *args, feature_switch: str, **kwargs):
        self.feature_switch = feature_switch
        super().__init__(*args, **kwargs)

    @property
    def hidden(self) -> bool:
        return self._hidden or not self.deployed()

    @hidden.setter
    def hidden(self, value: bool) -> None:
        self._hidden = bool(value)

    def deployed(self) -> bool:
        """Whether the invocation's profile holds the switch. A profile that cannot be read holds none."""
        try:
            return ProfileManager(invocation_profile_name()).has_feature_switch(self.feature_switch)
        except Exception:
            return False
