# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Workflow registry, run execution, failure -> run status mapping, and `vra resume`."""

from __future__ import annotations

import contextlib
import traceback
from typing import Any, Callable

from vra import errors, util
from vra.errors import (PermissionDenied, VraError, BlockedOnIT, RUNNING, SUCCEEDED, STATUS_TO_EXIT, FAILED)
from vra.state import (NON_RESUMABLE, LeaseKeeper, Run, StaleRun, lock_key_for_run, make_lock, owner_token)

WORKFLOWS: dict[str, Callable] = {}


def workflow(name: str):
    def deco(fn):
        WORKFLOWS[name] = fn
        return fn
    return deco


def _load_workflows() -> None:
    # import modules for their @workflow registrations
    from vra import (gitops, build, deploy, tests_runner, backup, restore, migrate, datacopy, watch,  # noqa: F401
                     preflight_cmd, iam)


class Outcome:
    def __init__(self, run: Run | None, exit_code: int, result: Any = None, message: str | None = None):
        self.run = run
        self.exit_code = exit_code
        self.result = result
        self.message = message

    def to_dict(self) -> dict:
        d = {"exitCode": self.exit_code, "message": self.message, "result": self.result}
        # convenience keys that CI and agents read without walking `result`
        res = self.result if isinstance(self.result, dict) else {}
        build_id = res.get("buildId") or (self.run.state.get("build") if self.run is not None else None)
        if build_id:
            d["buildId"] = build_id
        pr = ((res.get("publish") or {}).get("pr") or {}) if isinstance(res.get("publish"), dict) else {}
        if pr.get("url"):
            d["prUrl"] = pr["url"]
        if self.run is not None:
            d.update({"runId": self.run.run_id, "status": self.run.status, "workflow": self.run.state["workflow"],
                      "runDir": str(self.run.dir)})
            its = [i for i in self.run.state.get("itRequests", []) if i.get("status") == "open"]
            if its:
                d["itRequests"] = its
        return d


def _keeper(ctx, run: Run) -> LeaseKeeper:
    """The lease keeper of this process for ``run``: owner token (machine-local) + heartbeat."""
    if getattr(run, "leases", None) is None:
        run.leases = LeaseKeeper(ctx.backend.lock, owner_token(ctx.work_dir, run.run_id))
    run.backend = ctx.backend
    return run.leases


def start(ctx, name: str, args: dict, env: str | None = None, build: str | None = None) -> Outcome:
    _load_workflows()
    if name not in WORKFLOWS:
        raise errors.UsageError(f"unknown workflow {name}")
    try:
        run = Run.create(ctx.state_dir, name, args, ctx.initiator, run_id=ctx.run_id, env=env, build=build,
                         store=ctx.store, dry_run=ctx.dry_run, home=ctx.home)
    except VraError as exc:
        parked = getattr(exc, "run", None)
        if parked is None:
            raise
        # the first publish was refused: the run is parked NEEDS_HUMAN (never left RUNNING), report it
        ctx.run_id = parked.run_id
        return Outcome(parked, exc.exit_code, parked.state.get("result"), parked.state.get("message"))
    run.state["ledger"] = ctx.ledger_ref()
    if getattr(ctx, "identity", None) is not None:
        run.state["identity"] = ctx.identity.as_dict()
    run.save()
    ctx.run_id = run.run_id
    if hasattr(ctx.aws, "run_id"):
        ctx.aws.run_id = run.run_id
    return execute(ctx, run)


def _take_run_lease(ctx, run: Run) -> dict:
    keeper = _keeper(ctx, run)
    info = keeper.acquire(lock_key_for_run(run.run_id), run.run_id, takeover=ctx.takeover, dry_run=ctx.dry_run)
    if info.get("takeover"):
        run.state.setdefault("takeovers", []).append(
            {"at": util.now_iso(), "by": ctx.initiator, "lock": lock_key_for_run(run.run_id),
             "previous": (info.get("takeovers") or [{}])[-1]})
        run.save()
    return info


def human_authorization(ctx, action: str) -> list[dict]:
    """How a human-only ``action`` of this invocation was authorized (vra.human.require: tty or approval record)."""
    return [a for a in (getattr(ctx, "human_authorizations", None) or []) if a and a.get("action") == action]


def execute(ctx, run: Run, lease_taken: bool = False) -> Outcome:
    _load_workflows()
    fn = WORKFLOWS[run.state["workflow"]]
    keeper = _keeper(ctx, run)
    auth = [a for a in (getattr(ctx, "human_authorizations", None) or []) if a]
    if auth:
        run.state.setdefault("humanAuthorizations", []).extend({**a, "at": util.now_iso()} for a in auth)
        run.save()
    if not lease_taken:
        _take_run_lease(ctx, run)
    try:
        result = fn(ctx, run, run.state["args"])
        if result is not None:
            run.state["result"] = result
        if run.status == RUNNING:
            run.set_status(SUCCEEDED)
        else:
            run.save()
        code = STATUS_TO_EXIT.get(run.status, 1)
        return _finish(ctx, run, Outcome(run, code, run.state.get("result"), run.state.get("message")))
    except StaleRun as exc:
        # the stored run moved on elsewhere: report, never overwrite it
        return Outcome(run, exc.exit_code, exc.detail or None, exc.message)
    except PermissionDenied as exc:
        step_id = _current_step(run)
        req = handle_permission(ctx, run, step_id, exc)
        return _finish(ctx, run, Outcome(run, errors.BLOCKED_ON_IT, {"itRequest": req["id"],
                                                                     "path": str(run.it_dir / f"{req['id']}.md")},
                                         exc.message))
    except VraError as exc:
        run.state["result"] = {**(run.state.get("result") or {}), **({"detail": exc.detail} if exc.detail else {})}
        run.set_status(exc.run_status, exc.message)
        return _finish(ctx, run, Outcome(run, exc.exit_code, run.state.get("result"), exc.message))
    except KeyboardInterrupt:
        run.set_status(FAILED, "interrupted")
        raise
    except Exception as exc:  # noqa: BLE001
        run.state["result"] = {"traceback": util.redact(traceback.format_exc())[-4000:]}
        run.set_status(FAILED, f"{type(exc).__name__}: {util.redact(str(exc))}")
        return _finish(ctx, run, Outcome(run, errors.OTHER_ERROR, run.state["result"], run.state["message"]))
    finally:
        try:
            keeper.release(lock_key_for_run(run.run_id), run.run_id, dry_run=ctx.dry_run)
        except Exception as exc:  # noqa: BLE001
            util.eprint(f"warning: run lease release failed: {exc}")
        keeper.stop()


def handle_permission(ctx, run: Run, step_id: str, exc: PermissionDenied) -> dict:
    from vra import permissions
    sts = None
    try:
        if exc.env and not ctx.dry_run and any(d.get("kind") == "encoded" for d in exc.denials):
            sts = ctx.aws.client(exc.env, "sts", exc.role)
    except Exception:  # noqa: BLE001
        sts = None
    exc.denials = permissions.decode_encoded(sts, exc.denials)
    req = permissions.write_it_request(ctx, run, step_id, exc)
    s = run.find_step(step_id)
    if s is not None:
        s["status"] = "blocked"
        s["detail"] = f"{req['id']}: {exc.message}"
    run.set_status(errors.S_BLOCKED_ON_IT, f"{exc.message} -> {req['id']}")
    run.push_files(run.it_dir)
    return req


def _current_step(run: Run) -> str:
    for s in reversed(run.state["steps"]):
        if s["status"] in ("blocked", "running", "failed"):
            return s["id"]
    return run.state["steps"][-1]["id"] if run.state["steps"] else "start"


def _finish(ctx, run: Run, outcome: Outcome) -> Outcome:
    try:
        from vra import report
        report.write(ctx, run)
    except Exception as exc:  # noqa: BLE001 - a report failure must not mask the run result
        util.eprint(f"warning: report generation failed: {exc}")
    run.push_files(run.dir / "logs", run.junit_dir, run.dir / "conflicts.json")
    return outcome


def resume(ctx, run_id: str, confirm: bool = False) -> Outcome:
    """Re-run preflight for the blocked step, then continue from the first non-done step.

    The run lease is taken first: a run that is live on another machine is refused, and one whose holder went
    away needs ``--takeover`` once its lease expired (recorded in the run and the env history).
    ``confirm=True`` (``vra resume <id> --confirm``) records the resuming identity as having done the
    manual work the run is waiting for (``pendingConfirmations``), so those steps pass on this resume.
    """
    from vra import permissions
    run = Run.load(ctx.state_dir, run_id, store=ctx.store, home=ctx.home)
    if ctx.dry_run and not run.state.get("dryRun"):
        # resuming re-simulates IT requests, resets steps and runs the workflow on the stored run, and
        # every one of those saves and publishes it. `--dry-run` promises to change nothing, so it is refused here,
        # before the run lease, any save or any AWS call.
        raise errors.UsageError(
            f"`vra --dry-run resume` cannot preview run {run_id}: a resume changes the stored run "
            f"({run.status}). Inspect it with `vra report {run_id}`; a dry run of the workflow itself "
            f"(`vra --dry-run {run.state.get('workflow')} ...`) shows what it would do; resume without --dry-run")
    ctx.run_id = run.run_id
    if hasattr(ctx.aws, "run_id"):
        ctx.aws.run_id = run.run_id
    if run.state.get("dryRun") and not ctx.dry_run:
        ctx.dry_run = True
    if confirm and not run.state.get("pendingConfirmations"):
        raise errors.UsageError(f"run {run_id} is not waiting for a manual confirmation; drop --confirm")
    if run.status == SUCCEEDED:
        return Outcome(run, 0, run.state.get("result"), "run already SUCCEEDED; nothing to resume")
    if run.status in NON_RESUMABLE:
        raise errors.UsageError(
            f"run {run_id} ended {run.status}; it cannot be resumed (the env no longer runs this run's build). "
            f"Start a new run: `vra deploy` / `vra promote` for the build, or `vra rollback` "
            f"(ROLLBACK_FAILED: inspect the env first; `vra unlock` releases its lock)")
    _take_run_lease(ctx, run)
    keeper = run.leases
    try:
        if confirm:
            pending = run.state.get("pendingConfirmations", [])
            if not pending:
                raise errors.UsageError(f"run {run_id} is not waiting for a manual confirmation; drop --confirm")
            run.confirm_pending(ctx.initiator)
        # non-blocking requests (vra.buckets advisory checks) are re-checked by their own step, never block a resume
        open_reqs = [r for r in run.state.get("itRequests", []) if r.get("status") == "open" and r.get("blocking", True)]
        for req in open_reqs:
            still = permissions.resimulate(ctx, run, req)
            if still:  # still denied: IT request n+1 for exactly what is left
                req["status"] = "superseded"
                run.save()
                exc = PermissionDenied(f"still denied after {req['id']}: {len(still)} action(s)", still,
                                       principal=still[0].get("principal"), env=req.get("env"),
                                       role=req.get("role", "deploy"))
                new = handle_permission(ctx, run, req["step"], exc)
                return _finish(ctx, run, Outcome(run, errors.BLOCKED_ON_IT, {"itRequest": new["id"]}, exc.message))
            req["status"] = "resolved" if still is not None else "retrying"
            req["resolvedAt"] = util.now_iso()
            run.save()
        run.reset_for_resume()
        run.state.setdefault("resumes", []).append({"at": util.now_iso(), "by": ctx.initiator,
                                                    "takeover": bool(ctx.takeover), "ledger": ctx.ledger_ref()})
        run.save()
    except BaseException:
        keeper.release(lock_key_for_run(run.run_id), run.run_id, dry_run=ctx.dry_run)
        keeper.stop()
        raise
    return execute(ctx, run, lease_taken=True)


def mark_dirty(run: Run, env: str, what: str) -> None:
    """The env is mid-change (CloudFormation deploy started, tables being cleared/reloaded, migrations running).
    Recorded on the env lock itself, so a crash of this process cannot let another run in."""
    run.state.setdefault("dirtyEnvs", {})[env] = what
    run.save()
    if getattr(run, "leases", None) is not None:
        run.leases.set_dirty(env, what)


def mark_clean(run: Run, env: str) -> None:
    if (run.state.get("dirtyEnvs") or {}).pop(env, None) is not None:
        run.save()
        if getattr(run, "leases", None) is not None:
            run.leases.set_dirty(env, None)


@contextlib.contextmanager
def env_lock(ctx, run: Run, env: str):
    """Lock ``env`` for this run (take it BEFORE the gates, so nothing changes between check and use).

    Released on success, and on failure/park while the env is clean. A run that parks or fails while the env
    is dirty (see :func:`mark_dirty`) keeps the lock (``held``): `vra resume <run>` on this machine takes it back,
    `vra resume <run> --takeover` does so from another machine after the lease expired, and
    `vra unlock --env E --run R --reason ...` releases it and records that in the env history.
    """
    keeper = _keeper(ctx, run)
    info = keeper.acquire(env, run.run_id, env=env, takeover=ctx.takeover, dry_run=ctx.dry_run,
                          wait_seconds=int(getattr(ctx, "lock_wait_seconds", 0) or 0))
    run.state.setdefault("locks", {})[env] = {k: v for k, v in info.items() if k not in ("ownerHash", "takeovers")}
    if info.get("takeover"):
        run.state.setdefault("takeovers", []).append({"at": util.now_iso(), "by": ctx.initiator, "lock": env,
                                                      "previous": (info.get("takeovers") or [{}])[-1]})
        ctx.ledger.append_history(env, {"action": "takeover", "runId": run.run_id, "result": "SUCCEEDED",
                                        "by": ctx.initiator, "previous": (info.get("takeovers") or [{}])[-1],
                                        "dirty": info.get("dirty"),
                                        "authorization": human_authorization(ctx, "takeover")}, dry_run=ctx.dry_run)
    run.save()
    ok = False
    try:
        yield info
        ok = True
    finally:
        dirty = (run.state.get("dirtyEnvs") or {}).get(env)
        held = None
        if not ok and dirty and not ctx.dry_run:
            try:
                held = keeper.hold(env, run.run_id, f"{dirty}; run {run.run_id} parked/failed mid-change")
            except Exception as exc:  # noqa: BLE001
                util.eprint(f"warning: could not hold the lock for {env}: {exc}")
        if held:
            run.state.setdefault("heldLocks", {})[env] = {"since": util.now_iso(), "reason": dirty}
            # once per env, however often the run parks; and name the other-machine path (a CI retry
            # runs on a new host, so its resume is refused at this lock until a person unlocks or takes over)
            note = (f"{env} stays locked by this run ({dirty}): `vra resume {run.run_id}` continues on this "
                    f"machine; from another machine (e.g. a CI retry) a person first runs "
                    f"`vra unlock --env {env} --run {run.run_id} --reason ...` (releases it) or "
                    f"`vra resume {run.run_id} --takeover` (after the lease expired)")
            notes = run.state.setdefault("notes", [])
            notes[:] = [n for n in notes if not n.startswith(f"{env} stays locked by this run (")]
            notes.append(note)
        else:
            try:
                keeper.release(env, run.run_id, dry_run=ctx.dry_run)
            except Exception as exc:  # noqa: BLE001
                util.eprint(f"warning: lock release for {env} failed: {exc}")
            run.state.get("locks", {}).pop(env, None)
            (run.state.get("heldLocks") or {}).pop(env, None)
            # the lock is released, so an earlier park's "stays locked" note no longer holds (a resumed
            # run that succeeded still told the reader to unlock an env nobody held)
            notes = run.state.get("notes")
            if notes:
                notes[:] = [n for n in notes if not n.startswith(f"{env} stays locked by this run (")]
        run.save()


def unlock(ctx, env: str, run_id: str | None, reason: str) -> Outcome:
    """`vra unlock`: release an env lock left held by a parked/failed run. Recorded in the env history."""
    if not reason or not reason.strip():
        raise errors.UsageError("vra unlock needs --reason (it is recorded in the env history)")
    ctx.home.pull()
    lock = make_lock(ctx)
    holder = lock.holder(env)
    if not holder:
        return Outcome(None, 0, {"env": env, "lock": None}, f"{env} is not locked")
    if run_id and holder.get("runId") != run_id:
        raise errors.UsageError(f"{env} is locked by run {holder.get('runId')}, not {run_id}")
    if not run_id and not (holder.get("held") or holder.get("dirty")):
        raise errors.UsageError(f"{env} is locked by active run {holder.get('runId')}; pass --run to force")
    public = {k: v for k, v in holder.items() if k not in ("ownerHash",)}
    if not ctx.dry_run:
        lock.release(env, holder.get("runId") or "", force=True)
        ctx.ledger.append_history(env, {"action": "unlock", "runId": holder.get("runId"), "result": "SUCCEEDED",
                                        "by": ctx.initiator, "reason": reason.strip(), "lock": public,
                                        "authorization": human_authorization(ctx, "unlock")})
    return Outcome(None, 0, {"env": env, "released": public, "dryRun": ctx.dry_run},
                   f"released {env} lock of run {holder.get('runId')}")


__all__ = ["workflow", "start", "execute", "resume", "env_lock", "Outcome", "WORKFLOWS", "BlockedOnIT", "mark_dirty",
           "mark_clean", "unlock"]
