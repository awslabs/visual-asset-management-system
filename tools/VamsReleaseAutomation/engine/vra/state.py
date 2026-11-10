# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Run state, locks, env history and approval consumption.

Two backends behind one interface (:func:`make_backend`):

- the shared home folder (default, :mod:`vra.home`): run state, locks, history and consumed approvals are
  files under ``<home>/state/``, synced by the home's ``sync`` setting;
- ``stateStore.type: s3`` (optional): run state, history and consumed approvals in an S3 bucket with
  conditional writes, locks in a DynamoDB table with conditional updates.

Rules that hold for both:

- run state carries a ``rev``; a save whose ``rev`` is behind the stored copy is refused (:class:`StaleRun`);
- a lock carries the hash of its owner's token; the owner renews it (heartbeat), nobody re-enters it by run id
  alone, it is never released by expiry while ``dirty`` or ``held``, and ``--takeover`` (recorded) is the only
  way to continue a run whose holder went away;
- history records are write-once and numbered per env (``seq``); consumed approvals are write-once.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import socket
import threading
import time
from pathlib import Path
from typing import Any, Callable

from vra import util
from vra.errors import (RUNNING, NeedsHuman, UsageError, RUN_STATUSES, SUCCEEDED, S_BLOCKED_ON_IT,
                        S_NEEDS_HUMAN, FAILED)

STEP_STATUSES = ("pending", "running", "done", "failed", "blocked", "skipped")
TERMINAL_RUN = {SUCCEEDED, "ROLLED_BACK", "ROLLBACK_FAILED", FAILED, "TESTS_FAILED"}
# `vra resume` refuses these: the env was already put back (ROLLED_BACK) or needs a human (ROLLBACK_FAILED);
# resuming would re-run the failed deploy's later steps against an env that no longer runs that build.
# FAILED and TESTS_FAILED stay resumable (fix the cause, re-run the failed step).
NON_RESUMABLE = {SUCCEEDED, "ROLLED_BACK", "ROLLBACK_FAILED"}


class StaleRun(NeedsHuman):
    """The stored run state is newer than this process's copy: another process or machine changed the run."""


def new_run_id(workflow: str, now=None) -> str:
    t = (now or util.utcnow()).strftime("%Y%m%dT%H%M%SZ")
    return f"{workflow}-{t}-{secrets.token_hex(2)}"


def _legacy_home(state_dir: Path):
    from vra.home import Home
    return Home.legacy(state_dir, state_dir)


# ======================================================================= runs
class Run:
    """A run directory with ``state.json``. Every mutation is persisted immediately (atomic, compare-before-write)."""

    def __init__(self, work_dir: Path, run_id: str, state: dict, store: "StateStore | None" = None,
                 dry_run: bool = False, home=None, etag: str | None = None):
        self.work_dir = Path(work_dir)  # the state root: runs live under <state root>/runs
        self.run_id = run_id
        self.state = state
        self.store = store
        self.dry_run = dry_run
        self.home = home if home is not None else _legacy_home(self.work_dir)
        self._rev = int(state.get("rev") or 0)
        self._etag = etag
        self.backend: Any = None       # set by the runner: approval consumption, history
        self.leases: Any = None        # set by the runner: lock manager of this process

    # ------------------------------------------------------------- lifecycle
    @property
    def dir(self) -> Path:
        return self.work_dir / "runs" / self.run_id

    @classmethod
    def create(cls, work_dir: Path, workflow: str, args: dict, initiator: str, run_id: str | None = None,
               env: str | None = None, build: str | None = None, store=None, dry_run: bool = False,
               home=None) -> "Run":
        run_id = run_id or new_run_id(workflow)
        d = Path(work_dir) / "runs" / run_id
        state = {"runId": run_id, "rev": 0, "workflow": workflow, "args": args, "env": env, "build": build,
                 "initiator": initiator, "dryRun": dry_run, "startedAt": util.now_iso(), "endedAt": None,
                 "status": RUNNING, "steps": [], "itRequests": [], "approvals": [], "checkpoint": None,
                 "result": {}, "message": None, "children": [], "host": socket.gethostname()}
        run = cls(work_dir, run_id, state, store, dry_run, home)
        run.home.pull()
        if store is not None and not dry_run and store.run_exists(run_id):
            raise UsageError(f"run {run_id} already exists in the state store; use `vra resume {run_id}`")
        d.mkdir(parents=True, exist_ok=True)
        try:  # create-only: an explicit --run-id never overwrites another machine's run
            fd = os.open(str(d / "state.json"), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            raise UsageError(f"run {run_id} already exists; use `vra resume {run_id}`") from None
        os.close(fd)
        try:
            run.save(_creating=True)
        except NeedsHuman as exc:  # Contention (as StaleRun) or a refused push: the run never started
            run._park_refused_first_publish(exc)
            exc.run = run
            raise
        return run

    def _park_refused_first_publish(self, exc: NeedsHuman) -> None:
        """The run's first publish to the shared home was refused (rejected push, a person's unpushed commit, an
        unreachable remote). Without this the home kept a RUNNING run that nothing owns (live test O3): park it
        NEEDS_HUMAN with the reason, so the copy that reaches the remote later is never RUNNING."""
        reason = exc.message
        self.state["result"] = {"publishRefused": {"reason": reason, "at": util.now_iso()}}
        msg = (f"the run did not start: its first publish to the shared home was refused ({reason}). Fix the home "
               f"(`vra home sync`), then `vra resume {self.run_id}` or start the command again")
        try:
            self.set_status(S_NEEDS_HUMAN, msg)   # written locally; its own publish may be refused again
        except NeedsHuman:
            pass

    @classmethod
    def load(cls, work_dir: Path, run_id: str, store=None, home=None) -> "Run":
        home = home if home is not None else _legacy_home(Path(work_dir))
        home.pull()
        p = Path(work_dir) / "runs" / run_id / "state.json"
        etag = None
        if store is not None:
            got = store.get_run_state(run_id)  # the store is authoritative: a stale local copy never wins
            if got is not None:
                state, etag = got
                util.write_json(p, state)
        if not p.exists():
            raise UsageError(f"unknown run {run_id} (no {p})")
        try:
            state = util.read_json(p)
        except ValueError as exc:
            raise UsageError(f"run {run_id}: state.json is empty or corrupt ({exc})") from exc
        return cls(work_dir, run_id, state, store, bool(state.get("dryRun")), home, etag)

    @classmethod
    def latest(cls, work_dir: Path, workflow: str | None = None, status: str | None = None,
               home=None, store=None) -> "Run | None":
        runs_dir = Path(work_dir) / "runs"
        if home is not None:
            home.pull()
        if not runs_dir.exists():
            return None
        cands = []
        for d in runs_dir.iterdir():
            p = d / "state.json"
            if p.exists():
                try:
                    s = util.read_json(p)
                except ValueError:
                    continue
                if workflow and s.get("workflow") != workflow:
                    continue
                if status and s.get("status") != status:
                    continue
                cands.append((s.get("startedAt", ""), d.name))
        if not cands:
            return None
        return cls.load(work_dir, sorted(cands)[-1][1], store=store, home=home)

    def save(self, _creating: bool = False) -> None:
        p = self.dir / "state.json"
        with self.home.mutex(f"run-{self.run_id}"):
            if p.exists() and p.stat().st_size and not _creating:
                try:
                    disk = util.read_json(p)
                except ValueError:
                    disk = {}
                if int(disk.get("rev") or 0) != self._rev:
                    raise StaleRun(f"run {self.run_id} was changed by another process or machine (stored rev "
                                   f"{disk.get('rev')}, this copy {self._rev}); nothing was overwritten. Inspect "
                                   f"`vra status {self.run_id}` before resuming")
            self.state["rev"] = self._rev + 1
            if self.store is not None and not self.dry_run:
                self._etag = self.store.put_run_state(self.run_id, self.state, self._etag, create=_creating)
            util.write_json(p, self.state)
            self._rev += 1
        from vra.home import Contention
        try:
            self.home.publish(f"run {self.run_id} {self.state.get('status')}")
        except Contention as exc:
            raise StaleRun(f"run {self.run_id}: {exc.message}") from exc

    # ------------------------------------------------------------- paths
    def log_path(self, step_id: str) -> Path:
        return self.dir / "logs" / f"{step_id.replace('/', '_')}.log"

    @property
    def junit_dir(self) -> Path:
        return self.dir / "junit"

    @property
    def it_dir(self) -> Path:
        return self.dir / "it-requests"

    # ------------------------------------------------------------- status
    def set_status(self, status: str, message: str | None = None) -> None:
        if status not in RUN_STATUSES:
            raise ValueError(status)
        self.state["status"] = status
        if message is not None:
            self.state["message"] = message
        if status != RUNNING:
            self.state["endedAt"] = util.now_iso()
        self.save()

    @property
    def status(self) -> str:
        return self.state["status"]

    # ------------------------------------------------------------- steps
    def find_step(self, step_id: str) -> dict | None:
        for s in self.state["steps"]:
            if s["id"] == step_id:
                return s
        return None

    def _ensure_step(self, step_id: str, name: str | None) -> dict:
        s = self.find_step(step_id)
        if s is None:
            s = {"id": step_id, "name": name or step_id, "status": "pending", "startedAt": None, "endedAt": None,
                 "detail": None, "artifacts": [], "result": None, "attempts": 0}
            self.state["steps"].append(s)
        return s

    def step(self, step_id: str, fn: Callable[["StepContext"], Any], name: str | None = None,
             revalidate: bool = False) -> Any:
        """Execute a checkpointed step once. Done/skipped steps return their recorded result.

        ``revalidate=True`` (gates: approval, SoD, change window, promotion evidence) re-runs a done step on
        every execution, so a resumed run re-checks its gates instead of trusting a recorded ``done``.
        """
        s = self._ensure_step(step_id, name)
        if s["status"] == "skipped" or (s["status"] == "done" and not revalidate):
            return s.get("result")
        if s["status"] == "done" and revalidate:
            s.setdefault("revalidations", []).append(util.now_iso())
        s["status"] = "running"
        s["startedAt"] = util.now_iso()
        s["endedAt"] = None
        if s.get("detail"):  # a retried step must not keep showing the previous attempt's error
            s["previousDetail"] = s["detail"]
            s["detail"] = None
        s["attempts"] = int(s.get("attempts", 0)) + 1
        self.save()
        sc = StepContext(self, s)
        try:
            result = fn(sc)
        except StaleRun:
            raise
        except BaseException as exc:  # noqa: BLE001 - every failure is recorded, then re-raised
            s["status"] = getattr(exc, "step_status", "failed")
            s["detail"] = getattr(exc, "message", None) or str(exc) or type(exc).__name__
            s["endedAt"] = util.now_iso()
            self.save()
            raise
        s["status"] = "done"
        s["result"] = result
        s["endedAt"] = util.now_iso()
        self.save()
        return result

    def require_confirmation(self, step_id: str, message: str, detail: dict | None = None) -> dict:
        """Gate for work only a human can do (manual migration steps, a manual DNS switch).

        Returns the recorded confirmation when ``vra resume <run-id> --confirm`` was given for this step;
        otherwise records the step as awaiting confirmation and raises NeedsHuman (exit 20).
        """
        conf = self.state.get("confirmations", {}).get(step_id)
        if conf:
            return conf
        pending = self.state.setdefault("pendingConfirmations", [])
        if step_id not in pending:
            pending.append(step_id)
        self.save()
        raise NeedsHuman(f"{message}. When done, run `vra resume {self.run_id} --confirm`.",
                         {**(detail or {}), "awaitingConfirmation": step_id})

    def confirm_pending(self, by: str) -> list[str]:
        ids = list(self.state.get("pendingConfirmations", []))
        for sid in ids:
            self.state.setdefault("confirmations", {})[sid] = {"by": by, "at": util.now_iso()}
        self.state["pendingConfirmations"] = []
        self.save()
        return ids

    def skip(self, step_id: str, reason: str, name: str | None = None) -> None:
        s = self._ensure_step(step_id, name)
        if s["status"] in ("done", "skipped"):
            return
        s["status"] = "skipped"
        s["detail"] = reason
        s["endedAt"] = util.now_iso()
        self.save()

    def blocked_steps(self) -> list[dict]:
        return [s for s in self.state["steps"] if s["status"] in ("blocked", "failed", "running")]

    def reset_for_resume(self) -> list[str]:
        ids = []
        for s in self.state["steps"]:
            if s["status"] in ("blocked", "failed", "running"):
                s["status"] = "pending"
                ids.append(s["id"])
        self.state["status"] = RUNNING
        self.state["endedAt"] = None
        self.state["message"] = None
        self.save()
        return ids

    def step_result(self, step_id: str) -> Any:
        s = self.find_step(step_id)
        return s.get("result") if s else None

    def push_files(self, *paths: Path) -> None:
        """Mirror run files (logs, junit, IT requests, conflicts) to the S3 store; the home syncs them itself."""
        if self.store is None or self.dry_run:
            return
        for p in paths:
            p = Path(p)
            if p.is_dir():
                self.store.push_dir(p)
            elif p.is_file():
                self.store.push_file(p)


class StepContext:
    def __init__(self, run: Run, step: dict):
        self.run = run
        self.step = step

    @property
    def log_path(self) -> Path:
        return self.run.log_path(self.step["id"])

    def artifact(self, path: Path | str) -> None:
        p = str(path)
        if p not in self.step["artifacts"]:
            self.step["artifacts"].append(p)
            self.run.save()

    def detail(self, text: str) -> None:
        self.step["detail"] = text
        self.run.save()

    def log(self, text: str) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_path, "a", encoding="utf-8") as fh:
            fh.write(util.redact(text.rstrip("\n")) + "\n")


# ======================================================================= locks
def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:32]


def owner_token(work_dir: Path, run_id: str) -> str:
    """The run's owner token, kept on THIS machine only (``<work dir>/owner-tokens/<run-id>.token``, mode 600).
    The shared lock records only its hash, so another machine cannot present it."""
    p = Path(work_dir) / "owner-tokens" / f"{run_id}.token"
    if p.exists():
        t = p.read_text(encoding="utf-8").strip()
        if t:
            return t
    p.parent.mkdir(parents=True, exist_ok=True)
    t = secrets.token_hex(24)
    fd = os.open(str(p), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(t + "\n")
    return t


def lock_key_for_run(run_id: str) -> str:
    return f"run-{run_id}"


def _pid_alive(pid: Any) -> bool:
    try:
        os.kill(int(pid), 0)
    except (ProcessLookupError, ValueError, TypeError):
        return False
    except PermissionError:
        return True
    return True


def _decide(h: dict | None, run_id: str, th: str, takeover: bool, now: float, run_status: str | None,
            key: str) -> tuple[str, str | None]:
    """('take' | 'renew' | 'takeover' | 'refuse', why). Shared by the file and DynamoDB locks."""
    if not h:
        return "take", None
    if h.get("ownerHash") == th:
        return "renew", None
    dirty = h.get("dirty") or (h.get("held") and h.get("heldReason"))
    live = float(h.get("expiresAt") or 0) > now
    if live and h.get("host") == socket.gethostname() and h.get("pid") and not _pid_alive(h.get("pid")):
        live = False  # same machine, holder process is gone
    if not dirty and not live:
        return "take", None
    if not dirty and run_status is not None and run_status != RUNNING and h.get("runId") != run_id:
        return "take", None  # the holder run ended (crash before release) and left the env clean
    holder = h.get("runId")
    where = f"host {h.get('host')}" + (f" until {util.iso_from_epoch(h.get('expiresAt'))}" if live else "")
    if holder == run_id:
        if takeover and not live:
            return "takeover", None
        if takeover and live:
            return "refuse", (f"run {run_id} is still live on {where} (lease renewed {h.get('heartbeatAt')}); "
                              f"--takeover is only allowed after the lease expired")
        return "refuse", (f"run {run_id} holds {key} on {where} with another owner token"
                          + (" (lease expired)" if not live else "")
                          + f"; resume it on that machine, or `vra resume {run_id} --takeover` "
                            f"after the lease expired (recorded)")
    if dirty:
        return "refuse", (f"{key} is held by run {holder} mid-change ({dirty}) on host {h.get('host')}: "
                          f"`vra resume {holder}` continues it, `vra unlock --env {key} --run {holder} --reason ...` "
                          f"releases it; it is never released by expiry")
    return "refuse", f"{key} is locked by run {holder} on {where}"


class FileLock:
    """Locks as files in ``<state>/locks/<key>.lock``: create-only, owner token hash, heartbeat, ``dirty``."""

    def __init__(self, home_or_dir, clock: Callable[[], float] | None = None, lease_seconds: int | None = None):
        from vra.home import Home
        self.home = home_or_dir if isinstance(home_or_dir, Home) else _legacy_home(Path(home_or_dir))
        self.dir = self.home.locks_dir
        self.clock = clock or (lambda: time.time())
        self.lease = int(lease_seconds or self.home.lease_seconds)

    def _path(self, key: str) -> Path:
        return self.dir / f"{key}.lock"

    def holder(self, key: str) -> dict | None:
        p = self._path(key)
        if not p.exists():
            return None
        try:
            return util.read_json(p)
        except ValueError:  # corrupt or half-written lock: treated as held by nobody, never as ours
            return {"runId": None, "corrupt": True}

    def list(self) -> list[dict]:
        self.home.pull()
        if not self.dir.exists():
            return []
        out = []
        for p in sorted(self.dir.glob("*.lock")):
            h = self.holder(p.stem) or {}
            out.append({"key": p.stem, **h})
        return out

    def _run_status(self, h: dict | None) -> str | None:
        rid = (h or {}).get("runId")
        sp = self.home.runs_dir / str(rid) / "state.json" if rid else None
        if sp is None or not sp.exists():
            return None
        try:
            return util.read_json(sp).get("status")
        except ValueError:
            return None

    def _record(self, key: str, run_id: str, th: str, env: str | None, prev: dict | None) -> dict:
        now = self.clock()
        rec = {"key": key, "env": env, "runId": run_id, "ownerHash": th, "host": socket.gethostname(),
               "pid": os.getpid(), "acquiredAt": (prev or {}).get("acquiredAt") if (prev or {}).get("ownerHash") == th
               else util.now_iso(), "heartbeatAt": util.now_iso(), "expiresAt": now + self.lease,
               "dirty": (prev or {}).get("dirty"), "held": False, "heldReason": None,
               "takeovers": list((prev or {}).get("takeovers") or [])}
        return rec

    def acquire(self, key: str, run_id: str, token: str | None = None, env: str | None = None,
                takeover: bool = False, dry_run: bool = False, wait_seconds: int = 0) -> dict:
        token = token or secrets.token_hex(16)
        th = token_hash(token)
        deadline = self.clock() + max(0, int(wait_seconds))
        from vra.home import Contention
        rejected = 0
        while True:
            if rejected >= 5:
                raise NeedsHuman(f"could not take {key}: the shared home rejected {rejected} pushes in a row "
                                 f"(another machine keeps moving it, or sync.push fails); `vra status --all`")
            self.home.pull()
            with self.home.mutex(f"lock-{key}"):
                h = self.holder(key)
                verdict, why = _decide(h, run_id, th, takeover, self.clock(), self._run_status(h), key)
                if verdict == "refuse":
                    pass
                elif dry_run:
                    return {"dryRun": True, "key": key, "env": env, "runId": run_id}
                else:
                    rec = self._record(key, run_id, th, env, h if verdict in ("renew", "takeover") else None)
                    if verdict == "takeover":
                        rec["takeovers"].append({"at": util.now_iso(), "fromHost": (h or {}).get("host"),
                                                 "fromExpiresAt": (h or {}).get("expiresAt"),
                                                 "dirty": (h or {}).get("dirty")})
                    self.dir.mkdir(parents=True, exist_ok=True)
                    if h is None:
                        try:
                            fd = os.open(str(self._path(key)), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
                        except FileExistsError:
                            continue  # another local process won between the read and the create
                        with os.fdopen(fd, "w") as fh:
                            json.dump(rec, fh, indent=2)
                    else:
                        util.write_json(self._path(key), rec)
            if verdict != "refuse":
                try:
                    self.home.publish(f"lock {key} by {run_id}")
                except Contention:
                    rejected += 1
                    mine = self.holder(key)
                    if (mine or {}).get("ownerHash") == th and self.home.sync.type == "command":
                        self._path(key).unlink(missing_ok=True)
                    continue  # re-check against what the other machine wrote
                return {**rec, "takeover": verdict == "takeover", "token": token}
            if self.clock() >= deadline:
                raise NeedsHuman(why or f"{key} is locked", {"lock": _public(h)})
            time.sleep(min(15.0, max(0.05, deadline - self.clock())))

    def _update(self, key: str, token: str, fn: Callable[[dict], dict | None], message: str) -> dict | None:
        th = token_hash(token)
        with self.home.mutex(f"lock-{key}"):
            h = self.holder(key)
            if not h or h.get("ownerHash") != th:
                return None
            new = fn(dict(h))
            if new is None:
                self._path(key).unlink(missing_ok=True)
            else:
                util.write_json(self._path(key), new)
        try:
            self.home.publish(message)
        except Exception as exc:  # noqa: BLE001 - the caller decides; a lost renew is reported, not fatal
            util.eprint(f"warning: {message}: {exc}")
        return new if new is not None else {}

    def renew(self, key: str, token: str) -> dict | None:
        now = self.clock()
        return self._update(key, token, lambda h: {**h, "heartbeatAt": util.now_iso(), "expiresAt": now + self.lease,
                                                   "pid": os.getpid()}, f"heartbeat {key}")

    def set_dirty(self, key: str, token: str, what: str | None) -> dict | None:
        return self._update(key, token, lambda h: {**h, "dirty": what}, f"{key} dirty={bool(what)}")

    def hold(self, key: str, run_id: str, reason: str, token: str | None = None, dry_run: bool = False) -> dict | None:
        """Keep the lock of a parked run whose env is mid-change: never released by expiry."""
        if dry_run or token is None:
            return None
        return self._update(key, token, lambda h: {**h, "held": True, "heldReason": reason[:500],
                                                   "dirty": h.get("dirty") or reason[:200],
                                                   "heldAt": util.now_iso()}, f"hold {key}")

    def release(self, key: str, run_id: str, token: str | None = None, dry_run: bool = False,
                force: bool = False) -> bool:
        if dry_run:
            return False
        if force:
            with self.home.mutex(f"lock-{key}"):
                existed = self._path(key).exists()
                self._path(key).unlink(missing_ok=True)
            if existed:
                self.home.publish(f"unlock {key}")
            return existed
        if token is None:
            return False
        return self._update(key, token, lambda h: None, f"release {key}") is not None


def _public(h: dict | None) -> dict | None:
    return {k: v for k, v in h.items() if k != "token"} if h else None


LocalLock = FileLock  # name kept for callers that construct it from a work dir


class DynamoLock:
    """DynamoDB lock table (key ``lockId``: ``env#<env>`` / ``run#<id>``), read-decide-write with an item
    version, so two writers never both win. ``expiresAt`` is the table's TTL attribute: one week past the lease
    for a clean lock, 30 days for a dirty or held one (re-armed by every heartbeat)."""

    HOLD_SECONDS = 30 * 24 * 3600

    def __init__(self, client, table: str, ttl_seconds: int = 900, clock: Callable[[], float] | None = None):
        self.client = client
        self.table = table
        self.lease = int(ttl_seconds)
        self.clock = clock or (lambda: time.time())

    @staticmethod
    def _id(key: str) -> str:
        return f"run#{key[4:]}" if key.startswith("run-") else f"env#{key}"

    def _get(self, key: str) -> dict | None:
        from boto3.dynamodb.types import TypeDeserializer
        got = self.client.get_item(TableName=self.table, Key={"lockId": {"S": self._id(key)}}, ConsistentRead=True)
        it = got.get("Item")
        if not it:
            return None
        d = TypeDeserializer()
        return {k: d.deserialize(v) for k, v in it.items()}  # numbers stay Decimal: they are written back as is

    def holder(self, key: str) -> dict | None:
        h = self._get(key)
        if h is None:
            return None
        h = dict(h)
        for k in ("ver", "pid"):
            if h.get(k) is not None:
                h[k] = int(h[k])
        # callers read the lease end as expiresAt
        h["expiresAt"] = float(h.get("leaseUntil") or h.get("expiresAt") or 0)
        h.pop("leaseUntil", None)
        return h

    def list(self) -> list[dict]:
        return []  # a full table scan is not granted to the operator roles; `status --all` asks per env

    def _put(self, key: str, item: dict, prev_ver: int | None) -> bool:
        from boto3.dynamodb.types import TypeSerializer
        s = TypeSerializer()
        body = {k: s.serialize(v) for k, v in item.items() if v is not None}
        body["lockId"] = {"S": self._id(key)}
        kw: dict[str, Any] = {"TableName": self.table, "Item": body}
        if prev_ver is None:
            kw["ConditionExpression"] = "attribute_not_exists(lockId)"
        else:
            kw["ConditionExpression"] = "ver = :v"
            kw["ExpressionAttributeValues"] = {":v": {"N": str(prev_ver)}}
        try:
            self.client.put_item(**kw)
            return True
        except Exception as exc:  # noqa: BLE001
            if getattr(exc, "response", {}).get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return False
            raise

    def _item(self, key: str, run_id: str, th: str, env: str | None, prev: dict | None) -> dict:
        now = self.clock()
        dirty = (prev or {}).get("dirty")
        lease_until = now + self.lease
        return {"key": key, "env": env, "runId": run_id, "ownerHash": th, "host": socket.gethostname(),
                "pid": os.getpid(), "acquiredAt": util.now_iso(), "heartbeatAt": util.now_iso(),
                "leaseUntil": _dec(lease_until), "dirty": dirty,
                "expiresAt": int(now + (self.HOLD_SECONDS if dirty else self.lease + 7 * 24 * 3600)),
                "ver": int((prev or {}).get("ver") or 0) + 1, "takeovers": list((prev or {}).get("takeovers") or [])}

    def acquire(self, key: str, run_id: str, token: str | None = None, env: str | None = None,
                takeover: bool = False, dry_run: bool = False, wait_seconds: int = 0) -> dict:
        token = token or secrets.token_hex(16)
        th = token_hash(token)
        deadline = self.clock() + max(0, int(wait_seconds))
        while True:
            raw = self._get(key)
            h = self.holder(key) if raw else None
            verdict, why = _decide(h, run_id, th, takeover, self.clock(), None, key)
            if verdict != "refuse":
                if dry_run:
                    return {"dryRun": True, "key": key, "env": env, "runId": run_id}
                item = self._item(key, run_id, th, env, raw if verdict in ("renew", "takeover") else None)
                if verdict == "takeover":
                    item["takeovers"].append({"at": util.now_iso(), "fromHost": (raw or {}).get("host")})
                if raw and verdict == "take":
                    item["ver"] = int(raw.get("ver") or 0) + 1
                if self._put(key, item, int(raw.get("ver") or 0) if raw else None):
                    out = {**item, "expiresAt": float(item["leaseUntil"]), "takeover": verdict == "takeover",
                           "token": token}
                    return out
                continue
            if self.clock() >= deadline:
                raise NeedsHuman(why or f"{key} is locked", {"lock": _public(h)})
            time.sleep(min(15.0, max(0.05, deadline - self.clock())))

    def _update(self, key: str, token: str, fn: Callable[[dict], dict | None]) -> dict | None:
        th = token_hash(token)
        for _ in range(5):
            raw = self._get(key)
            if not raw or raw.get("ownerHash") != th:
                return None
            new = fn(dict(raw))
            if new is None:
                try:
                    self.client.delete_item(TableName=self.table, Key={"lockId": {"S": self._id(key)}},
                                            ConditionExpression="ver = :v",
                                            ExpressionAttributeValues={":v": {"N": str(int(raw.get("ver") or 0))}})
                    return {}
                except Exception as exc:  # noqa: BLE001
                    if getattr(exc, "response", {}).get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                        raise
                    continue
            new["ver"] = int(raw.get("ver") or 0) + 1
            if self._put(key, new, int(raw.get("ver") or 0)):
                return new
        return None

    def renew(self, key: str, token: str) -> dict | None:
        now = self.clock()

        def fn(h):
            h.update(heartbeatAt=util.now_iso(), leaseUntil=_dec(now + self.lease), pid=os.getpid())
            h["expiresAt"] = int(now + (self.HOLD_SECONDS if h.get("dirty") else self.lease + 7 * 24 * 3600))
            return h
        return self._update(key, token, fn)

    def set_dirty(self, key: str, token: str, what: str | None) -> dict | None:
        now = self.clock()

        def fn(h):
            h["dirty"] = what
            h["expiresAt"] = int(now + (self.HOLD_SECONDS if what else self.lease + 7 * 24 * 3600))
            return h
        return self._update(key, token, fn)

    def hold(self, key: str, run_id: str, reason: str, token: str | None = None, dry_run: bool = False) -> dict | None:
        if dry_run or token is None:
            return None
        now = self.clock()

        def fn(h):
            h.update(held=True, heldReason=reason[:500], dirty=h.get("dirty") or reason[:200],
                     expiresAt=int(now + self.HOLD_SECONDS))
            return h
        return self._update(key, token, fn)

    def release(self, key: str, run_id: str, token: str | None = None, dry_run: bool = False,
                force: bool = False) -> bool:
        if dry_run:
            return False
        if force:
            try:
                self.client.delete_item(TableName=self.table, Key={"lockId": {"S": self._id(key)}})
                return True
            except Exception:  # noqa: BLE001
                return False
        if token is None:
            return False
        return self._update(key, token, lambda h: None) is not None


def _dec(v: float):
    from decimal import Decimal
    return Decimal(str(round(v, 3)))


class LeaseKeeper:
    """One per process: holds the owner token, renews every lock it took (heartbeat thread), marks dirty."""

    def __init__(self, lock, token: str, interval: float | None = None):
        self.lock = lock
        self.token = token
        self.keys: dict[str, str] = {}
        self.lost: list[str] = []
        lease = getattr(lock, "lease", 900)
        self.interval = float(interval if interval is not None else max(1.0, lease / 3))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def acquire(self, key: str, run_id: str, env: str | None = None, takeover: bool = False, dry_run: bool = False,
                wait_seconds: int = 0) -> dict:
        info = self.lock.acquire(key, run_id, token=self.token, env=env, takeover=takeover, dry_run=dry_run,
                                 wait_seconds=wait_seconds)
        if not dry_run:
            self.keys[key] = run_id
            self._ensure_thread()
        return {k: v for k, v in info.items() if k != "token"}

    def _ensure_thread(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="vra-heartbeat", daemon=True)
            self._thread.start()

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            self.beat()

    def beat(self) -> None:
        for key in list(self.keys):
            try:
                if self.lock.renew(key, self.token) is None:
                    self.lost.append(key)
                    self.keys.pop(key, None)
                    util.eprint(f"warning: lock {key} is no longer held by this process")
            except Exception as exc:  # noqa: BLE001 - next beat retries
                util.eprint(f"warning: heartbeat for {key} failed: {exc}")

    def set_dirty(self, key: str, what: str | None) -> None:
        if key in self.keys:
            self.lock.set_dirty(key, self.token, what)

    def hold(self, key: str, run_id: str, reason: str) -> dict | None:
        out = self.lock.hold(key, run_id, reason, token=self.token)
        self.keys.pop(key, None)
        return out

    def release(self, key: str, run_id: str, dry_run: bool = False) -> None:
        self.lock.release(key, run_id, token=self.token, dry_run=dry_run)
        self.keys.pop(key, None)

    def stop(self) -> None:
        self._stop.set()


# ======================================================================= S3 mirror
class StateStore:
    """``stateStore.type: s3``: runs, history, consumed approvals, artifacts and checkpoints in
    ``s3://bucket/prefix/``. Run state is written with ``If-Match`` (or ``If-None-Match: *`` on create),
    bundles, signatures, checkpoints and history records create-only."""

    TOP_LEVEL = ("artifacts/", "checkpoints/")

    def __init__(self, client, bucket: str, prefix: str, work_dir: Path, top_dir: Path | None = None):
        self.client = client
        self.bucket = bucket
        self.prefix = (prefix or "vra").strip("/")
        self.work_dir = Path(work_dir)              # state root: runs/, history/, signatures/, sync-state.json
        self.top_dir = Path(top_dir or work_dir)    # parent of artifacts/ and checkpoints/

    def _key(self, rel: str) -> str:
        return f"{self.prefix}/{rel}"

    def _rel(self, path: Path) -> str:
        path = Path(path).resolve()
        top = self.top_dir.resolve()
        for d in self.TOP_LEVEL:
            if (top / d.rstrip("/")) in path.parents:
                return path.relative_to(top).as_posix()
        return path.relative_to(self.work_dir.resolve()).as_posix()

    def _local(self, rel: str) -> Path:
        return (self.top_dir if rel.startswith(self.TOP_LEVEL) else self.work_dir) / rel

    @staticmethod
    def _code(exc: BaseException) -> str:
        return str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))

    def push_file(self, path: Path, create_only: bool = False) -> bool:
        """Upload one file; ``create_only`` never overwrites (returns False when the object exists with other
        content, True when it was written or already identical)."""
        rel = self._rel(path)
        if not create_only:
            self.client.upload_file(str(path), self.bucket, self._key(rel))
            return True
        body = Path(path).read_bytes()
        try:
            self.client.put_object(Bucket=self.bucket, Key=self._key(rel), Body=body, IfNoneMatch="*")
            return True
        except Exception as exc:  # noqa: BLE001
            if self._code(exc) not in ("PreconditionFailed", "ConditionalRequestConflict"):
                raise
            return self.get_bytes(self._key(rel)) == body

    def push_run(self, run: Run) -> None:
        self.push_file(run.dir / "state.json")

    # live test: a bundle is ~15k files (three cdk.out targets); one request at a time took ~13 minutes
    TRANSFER_WORKERS = int(os.environ.get("VRA_STORE_WORKERS", "16") or 16)

    def _parallel(self, fn: Callable, items: list) -> list:
        """Run ``fn`` over ``items`` on a bounded thread pool (boto3 clients are thread-safe); results in item
        order; the first error is raised after the pool drains."""
        if len(items) <= 1 or self.TRANSFER_WORKERS <= 1:
            return [fn(i) for i in items]
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(self.TRANSFER_WORKERS, len(items))) as pool:
            futures = [pool.submit(fn, i) for i in items]
        return [f.result() for f in futures]

    def push_dir(self, d: Path, create_only: bool = False) -> list[str]:
        """Upload a tree; with ``create_only`` returns the files that exist in the store with other content.
        Files go up in parallel; a ``manifest.json`` at the top of the tree goes last, so a reader never takes a
        half-copied bundle for a complete one."""
        files = [p for p in sorted(Path(d).rglob("*")) if p.is_file()]
        last = [p for p in files if p.parent == Path(d) and p.name == "manifest.json"]
        first = [p for p in files if p not in last]
        ok = self._parallel(lambda p: self.push_file(p, create_only=create_only), first)
        ok += [self.push_file(p, create_only=create_only) for p in last]
        return [p.relative_to(d).as_posix() for p, good in zip(first + last, ok) if not good]

    def pull_prefix(self, rel_prefix: str) -> int:
        objs = []
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=self._key(rel_prefix)):
            objs.extend(page.get("Contents", []))

        def _get(obj):
            rel = obj["Key"][len(self.prefix) + 1:]
            dest = self._local(rel)
            dest.parent.mkdir(parents=True, exist_ok=True)
            self.client.download_file(self.bucket, obj["Key"], str(dest))

        self._parallel(_get, objs)
        return len(objs)

    def pull_run(self, run_id: str) -> None:
        self.pull_prefix(f"runs/{run_id}/")

    # ----------------------------------------------------------------- run state (compare-before-write)
    def run_exists(self, run_id: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=self._key(f"runs/{run_id}/state.json"))
            return True
        except Exception as exc:  # noqa: BLE001
            if self._code(exc) in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def get_run_state(self, run_id: str) -> tuple[dict, str] | None:
        try:
            got = self.client.get_object(Bucket=self.bucket, Key=self._key(f"runs/{run_id}/state.json"))
        except Exception as exc:  # noqa: BLE001
            if self._code(exc) in ("NoSuchKey", "404", "NotFound"):
                return None
            raise
        return json.loads(got["Body"].read()), got.get("ETag")

    def put_run_state(self, run_id: str, state: dict, etag: str | None, create: bool = False) -> str:
        kw: dict[str, Any] = {"Bucket": self.bucket, "Key": self._key(f"runs/{run_id}/state.json"),
                              "Body": json.dumps(state, indent=2, default=str).encode(),
                              "ContentType": "application/json"}
        if create:
            kw["IfNoneMatch"] = "*"
        elif etag:
            kw["IfMatch"] = etag
        else:
            got = self.get_run_state(run_id)
            if got is not None:
                stored_rev = int(got[0].get("rev") or 0)
                if stored_rev != int(state.get("rev") or 0) - 1:
                    raise StaleRun(f"run {run_id} in the state store is at rev {stored_rev}; this copy is behind")
                kw["IfMatch"] = got[1]
            else:
                kw["IfNoneMatch"] = "*"
        try:
            return self.client.put_object(**kw).get("ETag")
        except Exception as exc:  # noqa: BLE001
            if self._code(exc) in ("PreconditionFailed", "ConditionalRequestConflict"):
                raise StaleRun(f"run {run_id} was changed in the state store by another host; nothing was "
                               f"overwritten (`vra status {run_id}`)") from exc
            raise

    def put_bytes(self, rel: str, body: bytes, create_only: bool = False) -> None:
        kw: dict[str, Any] = {"Bucket": self.bucket, "Key": self._key(rel), "Body": body,
                              "ContentType": "application/json"}
        if create_only:
            kw["IfNoneMatch"] = "*"  # S3 conditional write: never overwrite an existing record
        self.client.put_object(**kw)

    def list_keys(self, rel_prefix: str) -> list[str]:
        out = []
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=self._key(rel_prefix)):
            out += [o["Key"] for o in page.get("Contents", [])]
        return sorted(out)

    def get_bytes(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()


# ======================================================================= history
def _hist_sort_key(r: dict):
    return (1, int(r["seq"]), "") if r.get("seq") is not None else (0, str(r.get("ts", "")), str(r.get("id", "")))


class FolderHistory:
    """``<state>/history/<env>/<seq:012d>-<run-id>.json``: write-once (create-only), numbered per env."""

    def __init__(self, home):
        self.home = home

    def dir(self, env: str) -> Path:
        return self.home.history_dir / env

    def files(self, env: str) -> list[Path]:
        d = self.dir(env)
        return sorted(p for p in d.glob("*.json")) if d.exists() else []

    def read(self, env: str) -> list[dict]:
        recs = []
        for p in self.files(env):
            try:
                rec = util.read_json(p)
            except ValueError:
                continue  # never trust a malformed record as evidence (`vra history verify` reports it)
            if isinstance(rec, dict):
                recs.append(rec)
        return sorted(recs, key=_hist_sort_key)

    def _next_seq(self, env: str) -> int:
        seqs = [int(p.name.split("-", 1)[0]) for p in self.files(env) if p.name.split("-", 1)[0].isdigit()]
        return max(seqs, default=0) + 1

    def append(self, env: str, rec: dict, keep_id: bool = False, publish: bool = True) -> dict:
        from vra.home import Contention
        run = str(rec.get("runId") or "none").replace("/", "_")
        for _ in range(20):
            with self.home.mutex(f"history-{env}"):
                seq = self._next_seq(env)
                out = {**rec, "seq": seq}
                p = self.dir(env) / f"{seq:012d}-{run}.json"
                p.parent.mkdir(parents=True, exist_ok=True)
                try:
                    fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
                except FileExistsError:
                    continue
                with os.fdopen(fd, "w") as fh:
                    json.dump(out, fh, indent=2, sort_keys=True, default=str)
            if not publish:
                return out
            try:
                self.home.publish(f"history {env} #{seq} {rec.get('action')}")
                return out
            except Contention:
                if p.exists() and self.home.sync.type == "command":
                    p.unlink()  # cannot be undone by the sync tool: take it back, renumber after the pull
                continue
        raise NeedsHuman(f"could not append to the history of {env}: the shared home kept moving")


class S3History:
    """``<prefix>/history/<env>/<seq:012d>-<run-id>.json`` in the state store, ``If-None-Match: *``. Records written
    before sequences existed (``<ts>-<id>.json``) sort first, by time."""

    def __init__(self, store: StateStore):
        self.store = store
        self._cache: dict[str, list[dict]] = {}

    def _rel(self, env: str) -> str:
        return f"history/{env}/"

    def _seq_of(self, key: str) -> int | None:
        name = key.rsplit("/", 1)[-1]
        head = name.split("-", 1)[0]
        return int(head) if head.isdigit() and len(head) == 12 else None

    def append(self, env: str, rec: dict, keep_id: bool = False, publish: bool = True) -> dict:
        body_rec = dict(rec)
        run = str(rec.get("runId") or "none").replace("/", "_")
        for _ in range(20):
            keys = self.store.list_keys(self._rel(env))
            seq = max([s for s in (self._seq_of(k) for k in keys) if s is not None], default=0) + 1
            out = {**body_rec, "seq": seq}
            try:
                self.store.put_bytes(f"{self._rel(env)}{seq:012d}-{run}.json",
                                     json.dumps(out, sort_keys=True, default=str).encode(), create_only=True)
            except Exception as exc:  # noqa: BLE001
                if StateStore._code(exc) in ("PreconditionFailed", "ConditionalRequestConflict"):
                    continue
                raise
            self._cache.pop(env, None)
            return out
        raise NeedsHuman(f"could not append to the history of {env} in the state store (contention)")

    def read(self, env: str) -> list[dict]:
        if env in self._cache:
            return list(self._cache[env])
        recs = []
        for key in self.store.list_keys(self._rel(env)):
            try:
                rec = json.loads(self.store.get_bytes(key))
            except ValueError:
                continue
            recs.append(rec)
        recs.sort(key=_hist_sort_key)
        self._cache[env] = recs
        return list(recs)


HistoryStore = S3History  # name kept for callers of the s3 backend


# ======================================================================= consumed approvals
class FolderConsumed:
    """``<state>/approvals-consumed/<env>/<sha256>.json``: an approval record authorizes one run."""

    def __init__(self, home):
        self.home = home

    def _path(self, env: str, sha: str) -> Path:
        return self.home.consumed_dir / env / f"{sha}.json"

    def get(self, env: str, sha: str) -> dict | None:
        p = self._path(env, sha)
        if not p.exists():
            return None
        try:
            return util.read_json(p)
        except ValueError:
            return {"runId": None, "corrupt": True}

    def claim(self, env: str, sha: str, rec: dict) -> dict:
        """Returns the consumption record that holds (ours, or the earlier run's)."""
        from vra.home import Contention
        for _ in range(5):
            self.home.pull()
            p = self._path(env, sha)
            p.parent.mkdir(parents=True, exist_ok=True)
            with self.home.mutex(f"consume-{sha}"):
                try:
                    fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
                except FileExistsError:
                    return self.get(env, sha) or {}
                with os.fdopen(fd, "w") as fh:
                    json.dump(rec, fh, indent=2, sort_keys=True, default=str)
            try:
                self.home.publish(f"approval {sha[:12]} used by {rec.get('runId')}")
                return rec
            except Contention:
                continue
        raise NeedsHuman("could not record the approval as used: the shared home kept moving")


    def replace(self, env: str, sha: str, rec: dict) -> dict:
        with self.home.mutex(f"consume-{sha}"):
            util.write_json(self._path(env, sha), rec)
        self.home.publish(f"approval {sha[:12]} claimed by {rec.get('runId')}")
        return rec


class S3Consumed:
    def __init__(self, store: StateStore):
        self.store = store

    def _rel(self, env: str, sha: str) -> str:
        return f"approvals-consumed/{env}/{sha}.json"

    def get(self, env: str, sha: str) -> dict | None:
        try:
            return json.loads(self.store.get_bytes(self.store._key(self._rel(env, sha))))
        except Exception as exc:  # noqa: BLE001
            if StateStore._code(exc) in ("NoSuchKey", "404", "NotFound"):
                return None
            raise

    def replace(self, env: str, sha: str, rec: dict) -> dict:
        self.store.put_bytes(self._rel(env, sha), json.dumps(rec, sort_keys=True, default=str).encode())
        return rec

    def claim(self, env: str, sha: str, rec: dict) -> dict:
        try:
            self.store.put_bytes(self._rel(env, sha), json.dumps(rec, sort_keys=True, default=str).encode(),
                                 create_only=True)
            return rec
        except Exception as exc:  # noqa: BLE001
            if StateStore._code(exc) in ("PreconditionFailed", "ConditionalRequestConflict"):
                return self.get(env, sha) or {}
            raise


# ======================================================================= backend
class Backend:
    """What a command needs from the state backend: home (paths, sync), store (s3 or None), lock, history,
    consumed approvals."""

    def __init__(self, home, store: StateStore | None, lock, history, consumed):
        self.home = home
        self.store = store
        self.lock = lock
        self.history = history
        self.consumed = consumed

    @property
    def kind(self) -> str:
        return "s3" if self.store is not None else "folder"


def make_store(ctx) -> StateStore | None:
    if not ctx.ledger.exists:
        return None
    ss = ctx.ledger.project.get("stateStore", {})
    if ss.get("type") != "s3":
        return None
    return StateStore(ctx.aws.tools_client("s3", ss.get("region")), ss["bucket"], ss.get("prefix") or "vra",
                      ctx.state_dir, ctx.home.artifacts_dir.parent)


def make_lock(ctx) -> Any:
    ss = ctx.ledger.project.get("stateStore", {}) if ctx.ledger.exists else {}
    if ss.get("type") == "s3":
        lease = ctx.home.lease_seconds
        if ctx.lock_client_factory:
            return DynamoLock(ctx.lock_client_factory(), ss["lockTable"], ttl_seconds=lease)
        return DynamoLock(ctx.aws.tools_client("dynamodb", ss.get("region")), ss["lockTable"], ttl_seconds=lease)
    return FileLock(ctx.home)


def make_backend(ctx) -> Backend:
    store = ctx.store
    lock = make_lock(ctx)
    if store is not None:
        ctx.home.sync.read_only = True  # the S3 store holds the state; the home repo is never pushed
        return Backend(ctx.home, store, lock, S3History(store), S3Consumed(store))
    return Backend(ctx.home, None, lock, FolderHistory(ctx.home), FolderConsumed(ctx.home))


__all__ = ["Run", "StepContext", "FileLock", "LocalLock", "DynamoLock", "LeaseKeeper", "StateStore", "S3History",
           "HistoryStore", "FolderHistory", "FolderConsumed", "S3Consumed", "Backend", "StaleRun", "new_run_id",
           "make_lock", "make_store", "make_backend", "owner_token", "token_hash", "lock_key_for_run",
           "S_BLOCKED_ON_IT", "S_NEEDS_HUMAN", "NON_RESUMABLE"]
