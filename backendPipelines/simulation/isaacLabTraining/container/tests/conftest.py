#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Pytest configuration for the simulation/isaacLabTraining container tests."""

import os
import re
import shlex
import sys

import pytest

_CONTAINER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _CONTAINER_DIR not in sys.path:
    sys.path.insert(0, _CONTAINER_DIR)

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")  # nosec B105 - test-only placeholder


def pytest_configure(config):
    config.addinivalue_line("markers", "unit: standalone unit test (no AWS calls)")


# What the image's Dockerfile hands its runtime user, read statically (no container build runs here;
# the image is over 15 GB). Used by test_isaaclab_nonroot_write_paths.py (issue #1312).

_DIRECTORY_DEFAULT_MODE = 0o755


def _under(path, root):
    path, root = os.path.normpath(path), os.path.normpath(root)
    return path == root or path.startswith(root.rstrip("/") + "/")


def _instructions(text):
    """``(KEYWORD, argument)`` per Dockerfile instruction, continuation lines joined."""
    instructions, pending = [], ""
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#") or (not line and not pending):
            continue
        if line.endswith("\\"):
            pending += line[:-1] + " "
            continue
        pending += line
        keyword, _, argument = pending.partition(" ")
        instructions.append((keyword.upper(), argument.strip()))
        pending = ""
    return instructions


def _commands(run_argument):
    """The simple commands of a shell-form RUN, split on ``&&``, ``||`` and ``;``."""
    for part in re.split(r"&&|\|\||;", run_argument):
        try:
            words = shlex.split(part)
        except ValueError:
            continue
        if words:
            yield words


def apply_chmod_mode(current, spec):
    """``chmod``'s effect of ``spec`` (octal, or symbolic such as ``g+w,o+t``) on a directory."""
    if re.fullmatch(r"[0-7]{3,4}", spec):
        return int(spec, 8)
    bits = {"u": {"r": 0o400, "w": 0o200, "x": 0o100, "s": 0o4000},
            "g": {"r": 0o040, "w": 0o020, "x": 0o010, "s": 0o2000},
            "o": {"r": 0o004, "w": 0o002, "x": 0o001}}
    mode = current
    for clause in spec.split(","):
        match = re.fullmatch(r"([ugoa]*)([+\-=])([rwxXst]*)", clause)
        if not match:
            continue
        who, op, perms = match.groups()
        who = (who or "a").replace("a", "ugo")
        mask = 0o1000 if "t" in perms else 0
        for w in who:
            for p in perms.replace("X", "x"):
                mask |= bits[w].get(p, 0)
        if op == "+":
            mode |= mask
        elif op == "-":
            mode &= ~mask
        else:
            mode = (mode & ~sum(sum(bits[w].values()) for w in who)) | mask
    return mode


class IsaacLabImage:
    """What the Dockerfile hands the runtime user before its ``USER`` line."""

    # The base image keeps Ubuntu's /tmp at mode 1777 (sticky, world-writable): the Isaac Lab 2.3.0
    # base only re-owns it (`chown -R isaac-sim /tmp`), so any user can create a folder there.
    world_writable = ("/tmp",)

    # Root-owned entries the Isaac Lab 2.3.0 base image ships in its working directory (the
    # `COPY ../ /workspace/isaaclab` layer, after Isaac Lab's .dockerignore, plus `_isaac_sim`).
    base_entries = {
        "/workspace/isaaclab": (
            ".dockerignore", ".flake8", ".gitattributes", ".pre-commit-config.yaml", ".vscode",
            "CITATION.cff", "CONTRIBUTING.md", "CONTRIBUTORS.md", "LICENSE", "LICENSE-mimic",
            "README.md", "SECURITY.md", "VERSION", "_isaac_sim", "apps", "docker", "docs",
            "environment.yml", "isaaclab.bat", "isaaclab.sh", "pyproject.toml", "pytest.ini",
            "scripts", "source", "tools",
        ),
    }

    def __init__(self, path):
        with open(path, encoding="utf-8") as f:
            instructions = _instructions(f.read())
        self.args = {}
        for keyword, argument in instructions:
            if keyword == "ARG" and "=" in argument:
                name, _, value = argument.partition("=")
                self.args.setdefault(name.strip(), value.strip())
        user_lines = [i for i, (keyword, _) in enumerate(instructions) if keyword == "USER"]
        assert user_lines, f"{path} declares no USER"
        self.runtime_user = instructions[user_lines[-1]][1]
        uid, _, gid = self.runtime_user.partition(":")
        runs = [argument for keyword, argument in instructions[:user_lines[-1]] if keyword == "RUN"]
        commands = [words for argument in runs for words in _commands(argument)]

        self.names = {uid}
        self.groups = {gid} if gid else set()
        for words in commands:
            if words[0] == "useradd" and "-u" in words and words[words.index("-u") + 1] == uid:
                self.names.add(words[-1])
            if words[0] == "groupadd" and "-g" in words and words[words.index("-g") + 1] == gid:
                self.groups.add(words[-1])

        self.created = []  # folders the build makes
        self.owned = []  # (folder, recursive) the build gives the runtime user
        self.group_of = {}  # folder -> group the build gives it
        self.mode_of = {}  # folder -> mode the build sets
        self.recursive_changes = []  # folders a recursive chown, chgrp or chmod covers
        for words in commands:
            arguments = [w for w in words[1:] if not w.startswith("-")]
            recursive = "-R" in words[1:] or "--recursive" in words[1:]
            if words[0] == "mkdir":
                self.created.extend(os.path.normpath(p) for p in arguments)
            elif words[0] == "chown" and arguments:
                owner, _, group = arguments[0].partition(":")
                for target in map(os.path.normpath, arguments[1:]):
                    if owner in self.names:
                        self.owned.append((target, recursive))
                    if group:
                        self.group_of[target] = group
                    if recursive:
                        self.recursive_changes.append(target)
            elif words[0] in ("chgrp", "chmod") and len(arguments) >= 2:
                for target in map(os.path.normpath, arguments[1:]):
                    if words[0] == "chgrp":
                        self.group_of[target] = arguments[0]
                    else:
                        current = self.mode_of.get(target, _DIRECTORY_DEFAULT_MODE)
                        self.mode_of[target] = apply_chmod_mode(current, arguments[0])
                    if recursive:
                        self.recursive_changes.append(target)

    def owns(self, folder):
        folder = os.path.normpath(folder)
        return any(path == folder or (recursive and _under(folder, path))
                   for path, recursive in self.owned)

    def group_writable(self, folder):
        """Whether the runtime user's group may create entries in ``folder``."""
        folder = os.path.normpath(folder)
        mode = self.mode_of.get(folder, _DIRECTORY_DEFAULT_MODE)
        return self.group_of.get(folder) in self.groups and mode & 0o030 == 0o030

    def sticky(self, folder):
        return bool(self.mode_of.get(os.path.normpath(folder), 0) & 0o1000)

    def writable(self, path):
        """Whether the runtime user can create and write ``path`` when the container runs.

        True under a world-writable folder; at a folder the user owns; below a folder owned
        recursively, or below one the build created empty for the user; directly inside a folder
        the user owns, which lets it create the entry; or a new entry directly inside a folder its
        group may write to (an entry the base image ships there stays root-owned).
        """
        path = os.path.normpath(path)
        if any(_under(path, root) for root in self.world_writable):
            return True
        for folder, recursive in self.owned:
            if path == folder or os.path.dirname(path) == folder:
                return True
            if _under(path, folder) and (recursive or folder in self.created):
                return True
        parent = os.path.dirname(path)
        if self.group_writable(parent):
            return os.path.basename(path) not in self.base_entries.get(parent, ())
        return False


@pytest.fixture(scope="module")
def image():
    """The Isaac Lab Dockerfile as its runtime user sees it."""
    return IsaacLabImage(os.path.join(_CONTAINER_DIR, "Dockerfile"))


@pytest.fixture
def chmod_mode():
    """The ``chmod`` mode model ``image`` applies, for its control test."""
    return apply_chmod_mode
