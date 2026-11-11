# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The shared home folder: one place for the ledger and the state, read by every machine and process.

Layout (``VRA_HOME``)::

    vra-home.yaml        home id, schema version, sync settings, registered hosts, lock lease
    ledger/              vra.yaml, envs/, approvals/, path-policy.yaml, tests/, core-patches.md
    state/
      locks/<key>.lock   owner token hash, host, run id, acquiredAt, heartbeatAt, expiresAt, dirty
      runs/<run-id>/     state.json (+ logs, junit, report, it-requests)
      history/<env>/<seq>-<run-id>.json      write-once, sequence per env
      approvals-consumed/<env>/<sha256>.json write-once
      sync-state.json    last seen upstream refs
    artifacts/           build bundles
    checkpoints/         checkpoint manifests
    .vra-local/          per-machine mutexes (never synced)

``sync.type`` decides how the folder reaches other machines: ``none`` (one machine; still the single place
every local process reads), ``git`` (``pull`` = ``git pull --ff-only``, ``push`` = commit + ``git push``) or
``command`` (customer ``pull`` / ``push`` commands). The engine pulls before it reads state or takes a lock
and pushes after it writes a lock, history, an approval consumption or run state. A rejected push means
another machine moved first: the local change is dropped, the folder is re-pulled, and the caller re-checks
(:class:`Contention`).

Without a home (``--ledger`` + ``--work-dir``), :meth:`Home.legacy` maps the same layout onto the work dir.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import random
import re
import shlex
import shutil
import socket
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterator

from vra import util
from vra.errors import ConfigError, NeedsHuman, UsageError

# Home.mutex bookkeeping: one RLock per (home, name) in this process, flock depth, open flock files
_MUTEX_GUARD = threading.Lock()
_MUTEX_RLOCKS: dict[tuple[str, str], threading.RLock] = {}
_MUTEX_DEPTH: dict[tuple[str, str], int] = {}
_MUTEX_FILES: dict[tuple[str, str], Any] = {}

HOME_FILE = "vra-home.yaml"
HOME_SCHEMA_VERSION = 1
DEFAULT_LEASE_SECONDS = 900
SYNC_TYPES = ("none", "git", "command")
HOME_GITIGNORE = ".vra-local/\n"
# State is never merged line by line: two machines that changed the same run, lock or record conflict, and the
# later push is dropped (Contention) instead of producing a JSON neither machine wrote.
HOME_GITATTRIBUTES = "state/** -merge -text\nartifacts/** -merge -text\ncheckpoints/** -merge -text\n"


class Contention(NeedsHuman):
    """A push was rejected: another machine changed the shared home first. The local change was dropped."""


def config_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "vra"


def host_id() -> str:
    """A stable id for this machine (``~/.config/vra/host-id``), registered in vra-home.yaml by ``setup --join``."""
    p = config_dir() / "host-id"
    if p.exists():
        v = p.read_text(encoding="utf-8").strip()
        if v:
            return v
    v = f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(v + "\n", encoding="utf-8")
    except OSError:
        pass
    return v


def pointer_path() -> Path:
    return config_dir() / "config.yaml"


def read_pointer() -> Path | None:
    p = pointer_path()
    if not p.exists():
        return None
    data = util.read_yaml(p) or {}
    return Path(data["home"]).expanduser() if isinstance(data, dict) and data.get("home") else None


def write_pointer(home_root: Path) -> Path:
    p = pointer_path()
    data = util.read_yaml(p) if p.exists() else {}
    data = data if isinstance(data, dict) else {}
    data["home"] = str(Path(home_root).resolve())
    util.write_yaml(p, data)
    return p


def _git(root: Path, *args: str, check: bool = True, timeout: int = 120) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    argv = ["git", "-C", str(root)]
    if not _git_identity_set(root):
        argv += ["-c", f"user.name=vra ({socket.gethostname()})", "-c", "user.email=vra@localhost.invalid"]
    p = subprocess.run([*argv, *args], capture_output=True, text=True, env=env, timeout=timeout)
    if check and p.returncode != 0:
        raise ConfigError(f"git {' '.join(args[:2])} in {root} failed: {util.redact((p.stderr or p.stdout).strip())}")
    return p


# a git host can refuse a push right after the previous one (its refs are still updating; a re-pull shows
# nothing new): retry with a short backoff and keep git's own error (live test)
PUSH_ATTEMPTS = 6
# pushes lost to another writer that moved the branch first: replayed and retried at once
PUSH_RACE_ATTEMPTS = 40


def _push_sleep(seconds: float) -> None:
    time.sleep(seconds)


def defer_push() -> bool:
    """``VRA_HOME_DEFER_PUSH=1``: commit what the engine writes but never push (a CI step that runs fork code
    and holds no home write token; the publish job pushes later)."""
    return os.environ.get("VRA_HOME_DEFER_PUSH") == "1"


def _git_identity_set(root: Path) -> bool:
    p = subprocess.run(["git", "-C", str(root), "config", "user.email"], capture_output=True, text=True)
    return p.returncode == 0 and bool(p.stdout.strip())


class Sync:
    """``sync`` of vra-home.yaml: how the folder is pulled and pushed."""

    def __init__(self, home: "Home", cfg: dict | None):
        cfg = dict(cfg or {})
        self.home = home
        self.type = cfg.get("type") or "none"
        if self.type not in SYNC_TYPES:
            raise ConfigError(f"{HOME_FILE}: sync.type {self.type!r} is not one of {list(SYNC_TYPES)}")
        self.pull_cmd = cfg.get("pull")
        self.push_cmd = cfg.get("push")
        self.branch = cfg.get("branch")
        if self.type == "command" and not (self.pull_cmd and self.push_cmd):
            raise ConfigError(f"{HOME_FILE}: sync.type command needs sync.pull and sync.push")
        # what the engine publishes: run state, locks, history, consumed approvals and checkpoints. The ledger and
        # vra-home.yaml change only by a person's commit; artifacts travel only when sync.pushArtifacts is set.
        self.scope = ["state", "checkpoints"] + (["artifacts"] if cfg.get("pushArtifacts") else [])
        # engine output that stays on this machine (never pushed, never a person's change): the build bundle when
        # artifacts do not travel. A CI build is not refused for its own bundle (live test)
        self.local_only = [] if cfg.get("pushArtifacts") else ["artifacts"]
        self.read_only = False  # stateStore s3: the home is read (ledger) but never pushed
        self.pulls = 0
        self.pushes = 0
        self.last_push_ok: bool | None = None   # sync.type command: did the last sync.push succeed

    @property
    def enabled(self) -> bool:
        return self.type != "none"

    # ----------------------------------------------------------------- git helpers
    def _has_upstream(self) -> bool:
        p = _git(self.home.root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", check=False)
        return p.returncode == 0 and bool(p.stdout.strip())

    def _run_cmd(self, cmd: str | list) -> subprocess.CompletedProcess:
        argv = shlex.split(cmd) if isinstance(cmd, str) else [str(x) for x in cmd]
        env = {**os.environ, "VRA_HOME": str(self.home.root)}
        return subprocess.run(argv, cwd=str(self.home.root), capture_output=True, text=True, env=env, timeout=600)

    # ----------------------------------------------------------------- pull / push
    def pull(self) -> None:
        if not self.enabled:
            return
        self.pulls += 1
        if self.type == "git":
            if not self._has_upstream():
                return
            p = _git(self.home.root, "pull", "--ff-only", "--quiet", check=False, timeout=300)
            if p.returncode != 0 and self._displace_unpublished(p.stderr or ""):
                p = _git(self.home.root, "pull", "--ff-only", "--quiet", check=False, timeout=300)
            if p.returncode != 0:
                raise NeedsHuman(f"cannot fast-forward the shared home {self.home.root}: "
                                 f"{util.redact((p.stderr or p.stdout).strip())[:400]}; resolve it with git, "
                                 f"then re-run (`vra home sync`)")
            return
        p = self._run_cmd(self.pull_cmd)
        if p.returncode != 0:
            raise NeedsHuman(f"sync.pull failed (exit {p.returncode}): {util.redact(p.stderr.strip())[:400]}")

    def _refuse_foreign_unpushed(self, message: str, extra: list[str]) -> None:
        """`git push` publishes the whole branch. When a person's local commit that changes the ledger (or anything
        outside the engine's scope) is not pushed yet, the engine's push would publish it for them, past review,
        and a gated command would then accept an approval record that was never pushed by a person (live test: an
        unpushed approval commit reached the remote with the run's first state push, then passed the gate)."""
        root = self.home.root
        p = _git(root, "diff", "--name-only", "@{u}", "HEAD", check=False)
        allowed = set(self.scope) | {str(x).rstrip("/") for x in extra}   # setup / join publish named paths
        if "." in allowed:
            return                                                             # `vra setup` publishes the home
        foreign = sorted({ln for ln in p.stdout.splitlines()
                          if ln and ln.split("/", 1)[0] not in allowed and ln not in allowed})
        if foreign:
            raise NeedsHuman(f"the shared home has local commits not pushed yet that change {', '.join(foreign[:5])}"
                             f"{' ...' if len(foreign) > 5 else ''}; the engine never publishes a person's change. "
                             f"Push it yourself (through review), then re-run ({message})",
                             {"unpushed": foreign[:50]})

    def _displace_unpublished(self, stderr: str) -> bool:
        """A process of this machine that died before it published leaves engine files that another machine has
        since committed (live test: machine A was killed mid-smoke, B took the run over and pushed the same log);
        `git pull --ff-only` then refuses. Files in the engine's scope are moved to ``.vra-local/displaced/``
        (kept, never deleted) and the remote's committed copy wins. Anything outside the scope stays a human's
        call. Returns True when something was moved."""
        m = re.search(r"(untracked working tree files would be overwritten by merge|"
                      r"local changes to the following files would be overwritten by merge):\n(.*?)\n(?:Please|Aborting)",
                      stderr, re.S)
        if not m:
            return False
        paths = [ln.strip() for ln in m.group(2).splitlines() if ln.strip()]
        if not paths or any(p.split("/", 1)[0] not in self.scope for p in paths):
            return False
        tracked = m.group(1).startswith("local changes")
        dest = self.home.local_dir / "displaced" / util.now_iso().replace(":", "")
        for rel in paths:
            src = self.home.root / rel
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            if src.exists():
                shutil.copy2(src, target)
            if tracked:
                _git(self.home.root, "checkout", "--", rel, check=False)
            else:
                src.unlink(missing_ok=True)
        util.eprint(f"warning: {len(paths)} unpublished engine file(s) of a process that ended before it published "
                    f"were moved to {dest} (another machine committed newer copies): {', '.join(paths[:5])}")
        return True

    def push(self, message: str, paths: list[str] | None = None) -> bool:
        """Publish local changes. Raises :class:`Contention` when another machine moved first (after dropping
        the local change and re-pulling). Returns False when there was nothing to publish."""
        if not self.enabled or self.read_only:
            return False
        if self.type == "command" and defer_push():
            self.deferred = getattr(self, "deferred", 0) + 1
            return True
        if self.type == "command":
            self.pushes += 1
            p = self._run_cmd(self.push_cmd)
            self.last_push_ok = p.returncode == 0
            if p.returncode != 0:
                self.pull()
                raise Contention(f"sync.push was rejected (exit {p.returncode}): another machine changed the "
                                 f"shared home first; re-checked after pulling")
            return True
        root = self.home.root
        if paths is None:
            outside = [ln for ln in _git(root, "status", "--porcelain", "--untracked-files=all").stdout.splitlines()
                       if ln[3:].split("/", 1)[0] not in self.scope
                       and ln[3:].split("/", 1)[0] not in self.local_only]
            from vra import identity
            if outside and identity.ci_provider():
                raise NeedsHuman("the shared home has changes outside the engine's scope "
                                 f"({', '.join(sorted({ln[3:] for ln in outside})[:5])}); in CI the engine never "
                                 f"publishes them. Discard them (fork code must not change the ledger)")
        scope = self.scope if paths is None else paths
        present = [s for s in scope if (root / s).exists()]
        if not present:
            return False
        _git(root, "add", "-A", "--", *present)
        if _git(root, "diff", "--cached", "--quiet", check=False).returncode == 0:
            return False
        _git(root, "commit", "-q", "-m", f"vra: {message}"[:200], "--no-verify")
        self.pushes += 1
        if not self._has_upstream():
            return True
        if defer_push():
            # a CI step that runs fork code holds no write token: the commit stays local and a separate publish
            # job (github-actions/vra-home-publish.yml) pushes the state files
            self.deferred = getattr(self, "deferred", 0) + 1
            return True
        self._refuse_foreign_unpushed(message, paths or [])
        last_err = ""
        refusals = races = 0
        while True:
            p = _git(root, "push", "-q", check=False, timeout=300)
            if p.returncode == 0:
                return True
            last_err = util.redact((p.stderr or "").strip())[-400:]
            before = _git(root, "rev-parse", "-q", "--verify", "@{u}", check=False).stdout.strip()
            # someone else pushed: replay our commit on top when it does not touch the same files
            if _git(root, "pull", "--rebase", "-q", check=False, timeout=300).returncode != 0:
                _git(root, "rebase", "--abort", check=False)
                # drop our commit (the worktree is clean, so --keep only moves HEAD and our files)
                _git(root, "reset", "-q", "--keep", "@{u}", check=False)
                raise Contention(f"push of the shared home was rejected ({message}): another machine changed "
                                 f"the same files first; the local change was dropped")
            after = _git(root, "rev-parse", "-q", "--verify", "@{u}", check=False).stdout.strip()
            if after != before:
                # another writer moved the branch (CI jobs publish about once a second): replayed on top, push
                # again at once with a little jitter, or two busy writers starve each other (live test)
                races += 1
                if races >= PUSH_RACE_ATTEMPTS:
                    break
                _push_sleep(random.uniform(0, 0.5))
                continue
            refusals += 1                     # nothing new upstream: the host refused for a moment
            if refusals >= PUSH_ATTEMPTS:
                break
            _push_sleep(min(2 ** (refusals - 1), 8))
        raise Contention(f"push of the shared home kept failing ({message}): {last_err or 'no output'}; "
                         f"run `vra home sync`")

    # ----------------------------------------------------------------- gates
    def gate_check(self) -> dict:
        """For approval-gated commands: the home is clean and HEAD equals the remote after a pull."""
        if self.type != "git":
            return {"sync": self.type}
        with self.home.mutex("sync"):
            return self._gate_check()

    def _gate_check(self) -> dict:
        self.pull()
        root = self.home.root
        dirty = _git(root, "status", "--porcelain", "--untracked-files=all", "--", "ledger").stdout.strip()
        if dirty:
            raise NeedsHuman("the shared home has uncommitted ledger changes; a gated action only runs on the "
                             f"reviewed ledger. Commit and push them first:\n{dirty[:800]}")
        head = _git(root, "rev-parse", "HEAD").stdout.strip()
        upstream = None
        if self._has_upstream():
            _git(root, "fetch", "-q", check=False, timeout=300)
            upstream = _git(root, "rev-parse", "@{u}").stdout.strip()
            if head != upstream and defer_push():
                # deferred engine commits (VRA_HOME_DEFER_PUSH): the ledger is still the pushed one when the local
                # commits sit on top of the remote and touch only the engine's own paths
                on_top = _git(root, "merge-base", "--is-ancestor", upstream, head, check=False).returncode == 0
                changed = [ln for ln in _git(root, "diff", "--name-only", upstream, head).stdout.splitlines() if ln]
                if on_top and all(ln.split("/", 1)[0] in self.scope for ln in changed):
                    return {"sync": "git", "head": head, "upstream": upstream, "deferred": len(changed)}
            if head != upstream:
                raise NeedsHuman(f"the shared home is at {head[:12]}, its remote at {upstream[:12]}; a gated action "
                                 f"only runs on the pushed ledger (`vra home sync`)")
        return {"sync": "git", "head": head, "upstream": upstream}

    def tracked(self, path: Path) -> bool | None:
        if self.type != "git":
            return None
        try:
            rel = Path(path).resolve().relative_to(self.home.root.resolve()).as_posix()
        except ValueError:
            return None  # not in the home (a CI platform's own approval record)
        return _git(self.home.root, "ls-files", "--error-unmatch", rel, check=False).returncode == 0

    def head(self) -> str | None:
        if self.type != "git":
            return None
        p = _git(self.home.root, "rev-parse", "HEAD", check=False)
        return p.stdout.strip() if p.returncode == 0 else None

    def publish_state(self) -> dict:
        """Whether this home's commits are on its remote, measured, never assumed: ``published`` is true only when
        the remote holds them; otherwise ``reason`` and ``publishCommand`` say why not and what publishes them.
        The engine never makes a home's first push (it would publish a person's ledger); a person does."""
        root = self.home.root
        q = shlex.quote(str(root))
        if self.type == "none":
            return {"published": False, "reason": "sync.type none: this home is not synced to other machines",
                    "publishCommand": None}
        if self.read_only:
            return {"published": False, "reason": "stateStore s3: the home is pulled for the ledger, never pushed",
                    "publishCommand": None}
        if self.type == "command":
            if self.last_push_ok:
                return {"published": True}
            return {"published": False, "reason": "sync.push has not run successfully yet",
                    "publishCommand": "vra home sync"}
        if not _git(root, "remote", check=False).stdout.split():
            return {"published": False, "reason": "the home has no git remote yet",
                    "publishCommand": f"git -C {q} remote add origin <url> && git -C {q} push -u origin HEAD"}
        if not self._has_upstream():
            return {"published": False, "reason": "no upstream branch: the home's first push has not happened",
                    "publishCommand": f"git -C {q} push -u origin HEAD"}
        ahead = _git(root, "rev-list", "--count", "@{u}..HEAD", check=False).stdout.strip()
        if ahead and ahead != "0":
            return {"published": False, "reason": f"{ahead} local commit(s) not pushed to the remote",
                    "publishCommand": "vra home sync"}
        return {"published": True}


class Home:
    """Paths of one shared home (or the legacy work-dir mapping) and its sync."""

    def __init__(self, root: Path, config: dict, ledger_dir: Path | None = None, state_dir: Path | None = None,
                 legacy: bool = False):
        self.root = Path(root).resolve()
        self.config = config or {}
        self.legacy = legacy
        self.ledger_dir = Path(ledger_dir).resolve() if ledger_dir else self.root / "ledger"
        self.state_dir = Path(state_dir).resolve() if state_dir else self.root / "state"
        self.sync = Sync(self, self.config.get("sync"))

    # ----------------------------------------------------------------- construction
    @classmethod
    def open(cls, root: Path | str) -> "Home":
        root = Path(root).expanduser().resolve()
        p = root / HOME_FILE
        if not p.exists():
            raise UsageError(f"{root} is not a vra home (no {HOME_FILE}); run `vra setup --home {root}` or "
                             f"`vra setup --join <path-or-git-url>`")
        cfg = util.read_yaml(p) or {}
        if not isinstance(cfg, dict):
            raise ConfigError(f"{p} must be a mapping")
        if int(cfg.get("schemaVersion") or 0) > HOME_SCHEMA_VERSION:
            raise ConfigError(f"{p}: schemaVersion {cfg.get('schemaVersion')} is newer than this vra supports "
                              f"({HOME_SCHEMA_VERSION}); upgrade vra")
        return cls(root, cfg)

    @classmethod
    def legacy(cls, ledger_root: Path | str, work_dir: Path | str) -> "Home":
        """No shared home: ledger where it is, state in the work dir (single machine)."""
        wd = Path(work_dir).resolve()
        return cls(wd, {"homeId": None, "sync": {"type": "none"}}, ledger_dir=Path(ledger_root), state_dir=wd,
                   legacy=True)

    @classmethod
    def create(cls, root: Path | str, sync: dict | None = None, lease_seconds: int = DEFAULT_LEASE_SECONDS) -> "Home":
        root = Path(root).expanduser().resolve()
        if (root / HOME_FILE).exists():
            raise UsageError(f"{root} is already a vra home; use `vra setup --join {root}`")
        cfg = {"schemaVersion": HOME_SCHEMA_VERSION, "homeId": f"home-{uuid.uuid4().hex[:12]}",
               "sync": dict(sync or {"type": "none"}), "locks": {"leaseSeconds": int(lease_seconds)}, "hosts": []}
        Sync(cls(root, cfg), cfg["sync"])  # validates
        root.mkdir(parents=True, exist_ok=True)
        for d in ("ledger", "state/locks", "state/runs", "state/history", "state/approvals-consumed", "artifacts",
                  "checkpoints"):
            (root / d).mkdir(parents=True, exist_ok=True)
            keep = root / d / ".gitkeep"
            if not any((root / d).iterdir()):
                keep.touch()
        util.atomic_write(root / ".gitignore", HOME_GITIGNORE)
        util.atomic_write(root / ".gitattributes", HOME_GITATTRIBUTES)
        util.write_yaml(root / HOME_FILE, cfg)
        return cls(root, cfg)

    # ----------------------------------------------------------------- paths
    @property
    def home_id(self) -> str | None:
        return self.config.get("homeId")

    @property
    def runs_dir(self) -> Path:
        return self.state_dir / "runs"

    @property
    def locks_dir(self) -> Path:
        return self.state_dir / "locks"

    @property
    def history_dir(self) -> Path:
        return self.state_dir / "history"

    @property
    def consumed_dir(self) -> Path:
        return self.state_dir / "approvals-consumed"

    @property
    def sync_state_path(self) -> Path:
        return self.state_dir / "sync-state.json"

    @property
    def artifacts_dir(self) -> Path:
        return (self.state_dir if self.legacy else self.root) / "artifacts"

    @property
    def checkpoints_dir(self) -> Path:
        return (self.state_dir if self.legacy else self.root) / "checkpoints"

    @property
    def local_dir(self) -> Path:
        """Per-machine files (mutexes); git-ignored, never synced."""
        return (self.state_dir if self.legacy else self.root) / ".vra-local"

    @property
    def lease_seconds(self) -> int:
        return int((self.config.get("locks") or {}).get("leaseSeconds") or DEFAULT_LEASE_SECONDS)

    def default_work_dir(self) -> Path:
        return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "vra" / (self.home_id or "home")

    # ----------------------------------------------------------------- mutex + sync
    @contextlib.contextmanager
    def mutex(self, name: str) -> Iterator[None]:
        """Exclusive lock among the processes of THIS machine (flock); other machines meet at the push.

        Reentrant within one thread (a publish that pulls again holds ``sync`` once); other threads of this
        process (the lease heartbeat) wait like other processes do."""
        with _MUTEX_GUARD:
            rlock = _MUTEX_RLOCKS.setdefault((str(self.root), name), threading.RLock())
        with rlock:
            key = (str(self.root), name)
            depth = _MUTEX_DEPTH.get(key, 0)
            fh = None
            if depth == 0:
                self.local_dir.mkdir(parents=True, exist_ok=True)
                fh = open(self.local_dir / f"{name.replace('/', '_')}.mutex", "a+")
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                _MUTEX_FILES[key] = fh
            _MUTEX_DEPTH[key] = depth + 1
            try:
                yield
            finally:
                _MUTEX_DEPTH[key] -= 1
                if _MUTEX_DEPTH[key] == 0:
                    f = _MUTEX_FILES.pop(key)
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                    f.close()

    def pull(self) -> None:
        # two vra processes of one machine share the home's git repository: their pulls, commits and pushes must
        # not interleave (live test: a heartbeat push and another run's pull collided on .git/index.lock)
        with self.mutex("sync"):
            self.sync.pull()

    def publish(self, message: str, paths: list[str] | None = None) -> bool:
        """Push what the engine wrote (``state/``, ``checkpoints/``; ``paths`` for setup and join)."""
        with self.mutex("sync"):
            return self.sync.push(message, paths)

    # ----------------------------------------------------------------- hosts
    def register_host(self, hid: str | None = None, role: str | None = None) -> dict:
        hid = hid or host_id()
        cfg = util.read_yaml(self.root / HOME_FILE) or {}
        hosts = cfg.setdefault("hosts", [])
        entry = next((h for h in hosts if h.get("id") == hid), None)
        if entry is None:
            entry = {"id": hid, "hostname": socket.gethostname(), "registeredAt": util.now_iso()}
            if role:
                entry["role"] = role
            hosts.append(entry)
            util.write_yaml(self.root / HOME_FILE, cfg)
            self.config = cfg
        return entry

    def describe(self) -> dict:
        return {"home": str(self.root), "homeId": self.home_id, "legacy": self.legacy,
                "ledger": str(self.ledger_dir), "state": str(self.state_dir), "sync": self.sync.type,
                "leaseSeconds": self.lease_seconds,
                "hosts": [h.get("id") for h in (self.config.get("hosts") or [])]}


def resolve(home_arg: str | None, ledger_arg: str | None, work_arg: str | None) -> tuple["Home", Path]:
    """Pick the home and the machine-local work dir for a CLI invocation.

    Order: ``--home`` / ``VRA_HOME``; then an explicit ``--ledger`` / ``VRA_LEDGER`` (legacy mode); then the
    pointer ``~/.config/vra/config.yaml`` written by ``vra setup``; then ``./vra-home.yaml``; else legacy
    ``./ledger`` + ``./.vra``.
    """
    home_path = home_arg or os.environ.get("VRA_HOME")
    ledger_path = ledger_arg or os.environ.get("VRA_LEDGER")
    work = work_arg or os.environ.get("VRA_WORK_DIR")
    if home_path:
        h = Home.open(home_path)
        if ledger_path and Path(ledger_path).resolve() != h.ledger_dir:
            raise UsageError(f"--ledger {ledger_path} conflicts with the home's ledger {h.ledger_dir}; drop --ledger")
        return h, Path(work).resolve() if work else h.default_work_dir()
    if ledger_path:
        return Home.legacy(ledger_path, work or "./.vra"), Path(work or "./.vra").resolve()
    ptr = read_pointer()
    if ptr is not None and (ptr / HOME_FILE).exists():
        h = Home.open(ptr)
        return h, Path(work).resolve() if work else h.default_work_dir()
    if Path(HOME_FILE).exists():
        h = Home.open(".")
        return h, Path(work).resolve() if work else h.default_work_dir()
    return Home.legacy("./ledger", work or "./.vra"), Path(work or "./.vra").resolve()


# ======================================================================= join / migrate / sync
def join(target: str, home_dir: str | None = None, branch: str | None = None) -> dict:
    """`vra setup --join <path|git-url>`: point this machine at an existing home (clone it when it is a URL)."""
    src = Path(target).expanduser()
    if (src / HOME_FILE).exists():
        root = src.resolve()
        cloned = False
    else:
        bare = src.exists() and (src / "HEAD").exists() and (src / "objects").is_dir()
        if src.exists() and not bare:
            raise UsageError(f"{src} exists but is not a vra home (no {HOME_FILE}) nor a git repository")
        root = Path(home_dir or Path.home() / "vams-release-home").expanduser().resolve()
        if (root / HOME_FILE).exists():
            raise UsageError(f"{root} is already a home; pass --home elsewhere or join it by path")
        if root.exists() and any(root.iterdir()):
            raise UsageError(f"{root} exists and is not empty")
        argv = ["git", "clone", "-q", *(["--branch", branch] if branch else []), target, str(root)]
        p = subprocess.run(argv, capture_output=True, text=True, timeout=600,
                           env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
        if p.returncode != 0:
            raise UsageError(f"git clone {target} failed: {util.redact(p.stderr.strip())[:400]}")
        if not (root / HOME_FILE).exists():
            raise UsageError(f"{target} has no {HOME_FILE} at its root; it is not a vra home")
        cloned = True
    h = Home.open(root)
    if h.sync.type == "git" and not cloned and not (root / ".git").exists():
        raise UsageError(f"{root} uses sync.type git but is not a git checkout")
    h.pull()
    entry = h.register_host()
    pushed = h.publish(f"register host {entry['id']}", paths=[HOME_FILE])
    ptr = write_pointer(h.root)
    from vra.ledger import Ledger
    val = Ledger(h.ledger_dir).validate() if (h.ledger_dir / "vra.yaml").exists() else \
        {"ok": False, "errors": ["the home has no ledger/vra.yaml yet"], "warnings": []}
    return {"home": str(h.root), "cloned": cloned, "host": entry, "pointer": str(ptr), "published": pushed,
            "sync": h.sync.type, "validation": val,
            "cron": f"17 6 * * * vra --home {h.root} --non-interactive --json watch >> {h.default_work_dir()}/watch.log 2>&1"}


def migrate(old_ledger: Path, old_work: Path | None, home_root: Path, sync: dict | None = None,
            dry_run: bool = False) -> dict:
    """`vra home migrate`: build a shared home from a 0.3 ledger checkout and its ``.vra`` work dir.

    Copies (never moves) the ledger without ``envs/*/history.jsonl``, turns every history line into one
    ``state/history/<env>/<seq>-<run-id>.json`` record (ordered by timestamp, sequence from 1), copies runs,
    artifacts, checkpoints, approvals in use and the sync state. Refuses while the old work dir holds a lock."""
    import shutil
    old_ledger = Path(old_ledger).expanduser().resolve()
    if not (old_ledger / "vra.yaml").exists():
        raise UsageError(f"{old_ledger} has no vra.yaml; pass the 0.3 ledger checkout with --from")
    old_work = Path(old_work).expanduser().resolve() if old_work else None
    held = []
    if old_work and (old_work / "locks").exists():
        for lf in sorted((old_work / "locks").glob("*.lock")):
            try:
                held.append({"env": lf.stem, **util.read_json(lf)})
            except ValueError:
                held.append({"env": lf.stem, "corrupt": True})
    if held:
        raise NeedsHuman(f"the old work dir holds lock(s) {[h['env'] for h in held]}; finish or `vra unlock` those "
                         f"runs with vra 0.3 before migrating", {"locks": held})
    plan: dict[str, Any] = {"home": str(Path(home_root).expanduser().resolve()), "ledger": [], "history": {},
                            "runs": 0, "artifacts": 0, "checkpoints": 0, "syncState": False, "dryRun": dry_run}
    files = [p for p in sorted(old_ledger.rglob("*")) if p.is_file() and ".git" not in p.relative_to(old_ledger).parts
             and not (p.name == "history.jsonl" and p.parent.parent.name == "envs")]
    plan["ledger"] = [p.relative_to(old_ledger).as_posix() for p in files]
    histories: dict[str, list[dict]] = {}
    for hp in sorted(old_ledger.glob("envs/*/history.jsonl")):
        recs = util.read_jsonl(hp)
        recs.sort(key=lambda r: (str(r.get("ts", "")), str(r.get("id", ""))))
        histories[hp.parent.name] = recs
        plan["history"][hp.parent.name] = len(recs)
    if old_work:
        plan["runs"] = len([d for d in (old_work / "runs").iterdir() if (d / "state.json").exists()]) \
            if (old_work / "runs").exists() else 0
        plan["artifacts"] = len(list((old_work / "artifacts").iterdir())) if (old_work / "artifacts").exists() else 0
        plan["checkpoints"] = len(list((old_work / "checkpoints").iterdir())) \
            if (old_work / "checkpoints").exists() else 0
        plan["syncState"] = (old_work / "sync-state.json").exists()
    if dry_run:
        return plan
    h = Home.create(home_root, sync)
    for p in files:
        dest = h.ledger_dir / p.relative_to(old_ledger)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dest)
    from vra.state import FolderHistory
    hist = FolderHistory(h)
    for env, recs in histories.items():
        for rec in recs:
            hist.append(env, {**rec, "migratedFrom": "history.jsonl"}, keep_id=True, publish=False)
    if old_work:
        for name, dest in (("runs", h.runs_dir), ("artifacts", h.artifacts_dir), ("checkpoints", h.checkpoints_dir)):
            srcd = old_work / name
            if srcd.exists():
                for child in sorted(srcd.iterdir()):
                    if child.is_dir():
                        shutil.copytree(child, dest / child.name, dirs_exist_ok=True)
                    else:
                        shutil.copy2(child, dest / child.name)
        if (old_work / "sync-state.json").exists():
            shutil.copy2(old_work / "sync-state.json", h.sync_state_path)
    h.register_host()
    if h.sync.type == "git" and not (h.root / ".git").exists():
        _git(h.root, "init", "-q")
    return {**plan, "created": str(h.root),
            "next": "review the copied ledger, then `vra --home <home> ledger validate` and `vra home sync`"}


def sync_now(h: Home) -> dict:
    """`vra home sync`: pull, then publish anything written locally."""
    h.pull()
    pushed = h.publish("vra home sync")
    return {"home": str(h.root), "sync": h.sync.type, "head": h.sync.head(), "published": pushed}


__all__ = ["Home", "Sync", "Contention", "resolve", "join", "migrate", "sync_now", "host_id", "write_pointer",
           "read_pointer", "HOME_FILE"]
