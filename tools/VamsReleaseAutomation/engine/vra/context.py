# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Execution context shared by every command. Tests inject fakes via the factory attributes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from vra import util
from vra.aws import AwsFactory
from vra.ledger import Ledger


class _History:
    """History for the ledger API: the state backend's history, with the ledger revision on every record."""

    def __init__(self, ctx: "Context"):
        self.ctx = ctx

    def enabled(self) -> bool:
        return True

    def read(self, env: str) -> list[dict]:
        return self.ctx.backend.history.read(env)

    def append(self, env: str, rec: dict) -> dict:
        rec = {**rec}
        rec.setdefault("ledger", self.ctx.ledger_ref())
        return self.ctx.backend.history.append(env, rec)


@dataclass
class Context:
    ledger: Ledger
    work_dir: Path
    dry_run: bool = False
    json: bool = False
    non_interactive: bool = False
    run_id: str | None = None
    # None = resolve from CI / STS (vra.identity); an explicit value (tests, embedding) is trusted as is
    initiator: str | None = None
    aws: Any = None
    shell_cls: type = util.Shell
    githost_factory: Callable[..., Any] | None = None
    lock_client_factory: Callable[[], Any] | None = None
    now: Callable[[], Any] = util.utcnow
    prompt: Callable[[str, str | None], str] | None = None
    home: Any = None                 # vra.home.Home; None = legacy (state in the work dir)
    takeover: bool = False           # `vra resume --takeover` (recorded)
    lock_wait_seconds: int = 0       # --lock-wait: wait for a busy env lock instead of failing at once
    _store: Any = None
    _store_loaded: bool = False
    _backend: Any = None
    _ledger_ref: Any = None

    def __post_init__(self):
        from vra import identity
        from vra.home import Home
        self.work_dir = Path(self.work_dir).resolve()
        if self.home is None:
            self.home = Home.legacy(self.ledger.root, self.work_dir)
        if self.aws is None:
            self.aws = AwsFactory(self.ledger, self.run_id)
        self.identity = identity.resolve(self.initiator)
        self.initiator = self.identity.initiator
        if isinstance(self.aws, AwsFactory):
            self.aws.source_identity_value = self.initiator
        # deploy/test/rollback history lives in the state backend, shared by every machine and CI job
        self.ledger.history_backend = _History(self)

    # --------------------------------------------------------------- dirs
    @property
    def state_dir(self) -> Path:
        """Root of runs/ and locks/: ``<home>/state`` (shared), or the work dir in legacy mode."""
        return self.home.state_dir

    @property
    def artifacts_dir(self) -> Path:
        return self.home.artifacts_dir

    @property
    def runs_dir(self) -> Path:
        return self.home.runs_dir

    @property
    def repo_dir(self) -> Path:
        fork = self.ledger.project.get("fork", {}) if self.ledger.exists else {}
        if fork.get("localPath"):
            return Path(fork["localPath"]).expanduser().resolve()
        return self.work_dir / "repo"

    @property
    def scratch_dir(self) -> Path:
        return self.work_dir / "scratch"

    @property
    def checkpoints_dir(self) -> Path:
        return self.home.checkpoints_dir

    @property
    def sync_state_path(self) -> Path:
        return self.home.sync_state_path

    def shell(self, log_path: Path | None = None) -> util.Shell:
        return self.shell_cls(log_path=log_path, dry_run=self.dry_run)

    @property
    def store(self):
        if not self._store_loaded:
            from vra.state import make_store
            self._store = make_store(self)
            self._store_loaded = True
        return self._store

    @property
    def backend(self):
        if self._backend is None:
            from vra.state import make_backend
            self._backend = make_backend(self)
        return self._backend

    def ledger_ref(self) -> dict:
        """The ledger revision a record or run was made with: the home's git commit (when synced by git) and a
        digest of the ledger tree (settings + approvals), so another machine can tell what authorized it."""
        if self._ledger_ref is None:
            hashes = util.tree_hashes(self.ledger.root, exclude=[".git"]) if self.ledger.root.exists() else {}
            hashes = {k: v for k, v in hashes.items() if not k.endswith("history.jsonl")}
            self._ledger_ref = {"digest": util.combined_hash(hashes)[:24], "commit": self.home.sync.head()}
        return self._ledger_ref

    def log(self, *args) -> None:
        util.eprint(*args)
