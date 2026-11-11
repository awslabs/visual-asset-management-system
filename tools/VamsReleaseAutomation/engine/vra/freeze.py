# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Write freeze for restore and data copy, and VAMS workflow executions.

A restore or a data copy clears the live tables and reloads them. Writes that land in that window are lost or
mixed into the reloaded data, so vra stops writes first and lets them resume at the end (also on failure).

Backends (``writeFreeze.backend`` in envs/<env>/env.yaml, or ``--write-freeze`` on the command line):

``maintenance-mode``
    Toggles the VAMS maintenance-mode setting: the API authorizer then denies every route except the amplify
    config, secure config and version routes. VAMS does not ship this switch yet; it is tracked in
    awslabs/visual-asset-management-system issue #1002. The backend is used only when the deployed stack
    publishes the switch (detected, never assumed): an SSM parameter under the env's resource-names prefix
    whose key starts with ``maintenanceMode`` (or the parameter named in ``writeFreeze.maintenanceModeParameter``).
``manual``
    The operator stops writes (announce the window, pause integrations and uploads) and vra records who
    confirmed it and when: ``--writes-stopped`` on the command, or ``vra resume <run> --confirm`` when the run
    parks waiting for it. Human-only (refused for agents). Allowed for every env; a gated env (prod) still needs
    its normal approval record.
``auto`` (default)
    ``maintenance-mode`` when the deployed VAMS supports it, otherwise ``manual``.

New backends implement :class:`WriteFreeze` and register in :data:`BACKENDS`.

Workflow executions: before a restore, a data copy, or a deploy with data migrations, the VAMS workflow
executions that are still running are listed and reported (step result + run note). They are aborted only
with an explicit ``--abort-workflow-executions`` flag, never automatically.
"""

from __future__ import annotations

import time
from typing import Any

from vra import backup, permissions, util
from vra.errors import NeedsHuman, PermissionDenied, UsageError

VAMS_MAINTENANCE_ISSUE = "awslabs/visual-asset-management-system#1002"
MAINTENANCE_KEY_PREFIX = "maintenanceMode"
DEFAULT_PROPAGATION_SECONDS = 30  # authorizer policy cache; overridable with writeFreeze.propagationSeconds


def _wrap(exc, service, env, resource=None):
    if permissions.is_denied(exc):
        raise PermissionDenied(f"{service} denied while managing the write freeze of {env}",
                               permissions.classify_exception(exc, service, resource), env=env) from exc
    raise exc


class WriteFreeze:
    """Interface. ``engage`` stops writes (or confirms they are stopped) and returns a JSON-able record;
    ``release`` lets writes resume. Both must be idempotent: a resumed run calls them again."""

    name = "base"

    def __init__(self, ctx, run, env: str, role: str, cfg: dict):
        self.ctx, self.run, self.env, self.role, self.cfg = ctx, run, env, role, cfg

    def supported(self) -> tuple[bool, str]:
        return True, ""

    def engage(self, step_id: str, purpose: str) -> dict:
        raise NotImplementedError

    def release(self, record: dict) -> dict:
        raise NotImplementedError


class MaintenanceModeFreeze(WriteFreeze):
    """VAMS maintenance mode (issue #1002). Detection only: if the deployed stack does not publish the switch,
    ``supported()`` is False and ``auto`` falls back to ``manual``."""

    name = "maintenance-mode"

    def _ssm(self):
        return self.ctx.aws.client(self.env, "ssm", self.role)

    def parameter(self) -> str | None:
        explicit = self.cfg.get("maintenanceModeParameter")
        ssm = self._ssm()
        try:
            if explicit:
                ssm.get_parameter(Name=explicit)
                return explicit
            from vra import vams_resources
            prefix = self.ctx.ledger.ssm_prefix(self.env).rstrip("/")
            names = vams_resources.resource_names(ssm, prefix)
        except Exception as exc:  # noqa: BLE001
            if permissions.is_denied(exc):
                _wrap(exc, "ssm", self.env)
            if "ParameterNotFound" in type(exc).__name__ or "ParameterNotFound" in str(exc):
                return None
            raise
        for key in sorted(names):
            if key.split("/")[-1].startswith(MAINTENANCE_KEY_PREFIX) or key.startswith(MAINTENANCE_KEY_PREFIX):
                return f"{prefix}/{key}"
        return None

    def supported(self) -> tuple[bool, str]:
        p = self.parameter()  # read-only detection, also in a dry run
        if not p:
            return False, (f"the deployed VAMS of {self.env} publishes no maintenance-mode switch (SSM "
                           f"{MAINTENANCE_KEY_PREFIX}* under {self.ctx.ledger.ssm_prefix(self.env)}); VAMS maintenance "
                           f"mode is tracked in {VAMS_MAINTENANCE_ISSUE}")
        return True, p

    def engage(self, step_id: str, purpose: str) -> dict:
        p = self.parameter()
        if not p:
            raise NeedsHuman(f"maintenance mode is not available on {self.env}; use writeFreeze.backend manual")
        ssm = self._ssm()
        try:
            cur = ssm.get_parameter(Name=p)["Parameter"]
            previous = cur.get("Value")
            if self.ctx.dry_run:
                return {"backend": self.name, "parameter": p, "previous": previous, "dryRun": True}
            if str(previous).lower() != "true":
                ssm.put_parameter(Name=p, Value="true", Type=cur.get("Type", "String"), Overwrite=True)
            after = ssm.get_parameter(Name=p)["Parameter"]["Value"]
        except Exception as exc:  # noqa: BLE001
            _wrap(exc, "ssm", self.env, p)
        if str(after).lower() != "true":
            raise NeedsHuman(f"maintenance mode on {self.env} did not turn on ({p} = {after!r})")
        wait = int(self.cfg.get("propagationSeconds", DEFAULT_PROPAGATION_SECONDS))
        if wait and not self.ctx.dry_run and float(backup._poll_seconds()) > 0:  # VRA_POLL_SECONDS=0 in tests
            time.sleep(min(wait, 600))
        return {"backend": self.name, "parameter": p, "previous": previous, "engagedAt": util.now_iso(),
                "by": self.ctx.initiator, "purpose": purpose, "propagationSeconds": wait}

    def release(self, record: dict) -> dict:
        p = record.get("parameter")
        if not p or record.get("dryRun"):
            return {"released": False, "reason": "nothing engaged"}
        restore_to = "true" if str(record.get("previous")).lower() == "true" else "false"
        ssm = self._ssm()
        try:
            cur = ssm.get_parameter(Name=p)["Parameter"]
            ssm.put_parameter(Name=p, Value=restore_to, Type=cur.get("Type", "String"), Overwrite=True)
        except Exception as exc:  # noqa: BLE001
            _wrap(exc, "ssm", self.env, p)
        return {"released": True, "parameter": p, "value": restore_to, "at": util.now_iso()}


class ManualFreeze(WriteFreeze):
    """The operator stops writes; vra records who confirmed it and when (the acknowledgement)."""

    name = "manual"

    def engage(self, step_id: str, purpose: str) -> dict:
        st = self.run.state
        ack = st.get("writeFreezeAck")
        if ack and not ack.get("used"):
            ack["used"] = True
            self.run.save()
            # the gate has resolved the initiator by now (CI platform / STS); attribute the acknowledgement to that
            # identity, keeping the name asserted on the command line when it differs
            by = self.ctx.initiator or ack["by"]
            return {"backend": self.name, "acknowledgedBy": by, "acknowledgedAt": ack["at"], "source": ack["source"],
                    "purpose": purpose, **({"assertedBy": ack["by"]} if ack["by"] != by else {})}
        if self.ctx.dry_run:
            return {"backend": self.name, "dryRun": True, "note": "a real run needs --writes-stopped or "
                                                                   "`vra resume --confirm`"}
        conf = self.run.require_confirmation(
            step_id, f"stop all writes to {self.env} before the {purpose} (VAMS has no maintenance mode in this "
                     f"version, see {VAMS_MAINTENANCE_ISSUE}): announce the window, pause integrations, uploads and "
                     f"pipelines, then confirm. Or re-run with --writes-stopped")
        return {"backend": self.name, "acknowledgedBy": conf["by"], "acknowledgedAt": conf["at"],
                "source": "resume --confirm", "purpose": purpose}

    def release(self, record: dict) -> dict:
        # a later engage (resume after a failure) must ask again: the old confirmation no longer holds
        sid = record.get("stepId")
        if sid:
            (self.run.state.get("confirmations") or {}).pop(sid, None)
            self.run.save()
        return {"released": True, "note": f"writes to {self.env} may resume (manual freeze)", "at": util.now_iso()}


BACKENDS: dict[str, type[WriteFreeze]] = {"maintenance-mode": MaintenanceModeFreeze, "manual": ManualFreeze}


def config(ctx, env: str) -> dict:
    return dict(ctx.ledger.env(env).get("writeFreeze") or {})


def resolve(ctx, run, env: str, role: str, override: str | None = None) -> tuple[WriteFreeze, dict]:
    """Pick the backend: ``--write-freeze`` > envs/<env> writeFreeze.backend > auto."""
    cfg = config(ctx, env)
    wanted = override or cfg.get("backend") or "auto"
    if wanted not in ("auto", *BACKENDS):
        raise UsageError(f"unknown write-freeze backend {wanted!r}; known: auto, {', '.join(BACKENDS)}")
    detection: dict[str, Any] = {"requested": wanted}
    if wanted in ("auto", "maintenance-mode"):
        mm = MaintenanceModeFreeze(ctx, run, env, role, cfg)
        ok, why = mm.supported()
        detection["maintenanceMode"] = {"supported": ok, ("parameter" if ok else "reason"): why}
        if ok:
            return mm, detection
        if wanted == "maintenance-mode":
            raise NeedsHuman(f"--write-freeze maintenance-mode: {why}", detection)
        return ManualFreeze(ctx, run, env, role, cfg), detection
    return BACKENDS[wanted](ctx, run, env, role, cfg), detection


def record_ack(run, ctx, writes_stopped: bool) -> None:
    """``--writes-stopped``: the initiator confirms that writes are stopped (recorded who/when, used once)."""
    if writes_stopped and not run.state.get("writeFreezeAck"):
        run.state["writeFreezeAck"] = {"by": ctx.initiator, "at": util.now_iso(), "source": "--writes-stopped"}
        run.save()


class Frozen:
    """Context manager around the clear-and-reload part of a restore / data copy.

    ``with Frozen(...) as fr:`` engages the freeze (a checkpointed step that re-runs on resume) and releases it
    on exit, also when the body raises (failure, permission park, NeedsHuman). The engage/release records are kept
    in ``run.state['writeFreeze']`` and returned for the history record.
    """

    def __init__(self, ctx, run, env: str, step_id: str, purpose: str, role: str = "deploy",
                 override: str | None = None, unattended: bool = False, complete_step: str | None = None):
        self.ctx, self.run, self.env, self.step_id, self.purpose = ctx, run, env, step_id, purpose
        self.role, self.override, self.unattended = role, override, unattended
        # last step of the frozen part: once it is done, a resumed run does not freeze (or ask) again
        self.complete_step = complete_step
        self.backend: WriteFreeze | None = None
        self.record: dict = {}

    def __enter__(self):
        done = self.complete_step and (self.run.find_step(self.complete_step) or {}).get("status") == "done"
        if done:
            prev = ((self.run.state.get("writeFreeze") or {}).get(self.env) or {}).get("engaged") or {}
            self.record = {**prev, "resumeSkipped": f"frozen part already complete ({self.complete_step})"}
            self.backend = None   # nothing to engage or release again
            st = self.run.find_step(self.step_id)
            if st and st["status"] != "done":   # an earlier resume parked here asking again; that no longer applies
                st.update({"status": "done", "detail": self.record["resumeSkipped"], "endedAt": util.now_iso()})
            pend = self.run.state.get("pendingConfirmations") or []
            if self.step_id in pend:
                pend.remove(self.step_id)
            self.run.save()
            return self

        def _engage(sc):
            backend, detection = resolve(self.ctx, self.run, self.env, self.role, self.override)
            self.backend = backend
            if self.unattended and backend.name == "manual":
                gated = bool(self.ctx.ledger.env(self.env).get("requiresApproval"))
                if gated:
                    raise NeedsHuman(f"an unattended {self.purpose} of {self.env} needs a write freeze and only the "
                                     f"manual backend is available; a human must run it", detection)
                rec = {"backend": "none", "unattended": True, "detection": detection,
                       "warning": f"unattended {self.purpose} of ungated env {self.env}: writes were not frozen "
                                  f"(manual backend needs a human)"}
                self.run.state.setdefault("notes", []).append(rec["warning"])
                return rec
            rec = backend.engage(sc.step["id"], self.purpose)
            return {**rec, "stepId": sc.step["id"], "detection": detection}

        self.record = self.run.step(self.step_id, _engage, f"write freeze ({self.purpose})", revalidate=True)
        if self.backend is None and self.record.get("backend") in BACKENDS:
            self.backend = BACKENDS[self.record["backend"]](self.ctx, self.run, self.env, self.role,
                                                            config(self.ctx, self.env))
        wf = self.run.state.setdefault("writeFreeze", {})
        wf[self.env] = {"engaged": self.record, "released": None}
        self.run.save()
        return self

    def __exit__(self, exc_type, exc, tb):
        rel: dict
        if self.record.get("resumeSkipped"):
            return False          # keep the original engage / release records
        if self.backend is None or self.record.get("backend") == "none":
            rel = {"released": False, "reason": "no freeze engaged"}
        else:
            try:
                rel = self.backend.release(self.record)
            except Exception as rexc:  # noqa: BLE001 - never mask the body's own failure
                rel = {"released": False, "error": getattr(rexc, "message", None) or str(rexc)}
                self.run.state.setdefault("notes", []).append(
                    f"write freeze of {self.env} could NOT be lifted ({rel['error']}); lift it by hand "
                    f"({self.record.get('parameter') or self.record.get('backend')})")
                if exc is None:
                    self.run.state.setdefault("writeFreeze", {}).setdefault(self.env, {})["released"] = rel
                    self.run.save()
                    raise
        rel["onFailure"] = exc is not None
        self.run.state.setdefault("writeFreeze", {}).setdefault(self.env, {})["released"] = rel
        self.run.save()
        return False

    def history_fields(self) -> dict:
        r = self.record or {}
        return {"writeFreeze": {k: r.get(k) for k in ("backend", "acknowledgedBy", "acknowledgedAt", "source",
                                                      "parameter", "engagedAt", "unattended") if r.get(k) is not None}}


# ===================================================================== workflow executions
TERMINAL = ("SUCCEEDED", "FAILED", "ABORTED", "TIMED_OUT")
EXECUTION_TABLE_KEYS = ("dynamoTables/workflowExecutionsStorageV2", "dynamoTables/workflowExecutionsStorage")
ARN_ATTRS = ("workflow_execution_arn", "workflowExecutionArn", "executionArn", "execution_arn")


def _plain(item: dict) -> dict:
    return {k: list(v.values())[0] for k, v in item.items() if isinstance(v, dict) and len(v) == 1}


def active_workflow_executions(ctx, env: str, role: str = "deploy", names: dict | None = None,
                               limit: int = 500) -> dict:
    """Running VAMS workflow executions of ``env``: rows of the workflow-executions table whose status is not
    terminal, confirmed with Step Functions DescribeExecution (the table can lag behind). Read-only."""
    from vra import vams_resources
    if names is None:
        names = vams_resources.discover(ctx, env, role)
    table = next((names["tables"][k] for k in EXECUTION_TABLE_KEYS if k in names["tables"]), None)
    if not table:
        return {"skipped": "no workflow-executions table under the SSM prefix", "active": []}
    ddb = ctx.aws.client(env, "dynamodb", role)
    sfn = ctx.aws.client(env, "stepfunctions", role)
    active: list[dict] = []
    scanned = 0
    kw: dict[str, Any] = {"TableName": table}
    try:
        while True:
            resp = ddb.scan(**kw)
            for it in resp.get("Items", []):
                scanned += 1
                row = _plain(it)
                if str(row.get("executionStatus", "")).upper() in TERMINAL:
                    continue
                arn = next((row[a] for a in ARN_ATTRS if row.get(a)), None)
                status = row.get("executionStatus") or "UNKNOWN"
                if arn:
                    try:
                        status = sfn.describe_execution(executionArn=arn).get("status", status)
                    except Exception as exc:  # noqa: BLE001
                        if permissions.is_denied(exc):
                            raise
                        if "ExecutionDoesNotExist" in str(exc):
                            continue
                        raise
                if str(status).upper() in TERMINAL:
                    continue
                active.append({"executionId": row.get("workflowExecutionId") or row.get("executionId"),
                               "workflowId": row.get("workflowId"), "databaseId": row.get("workflowDatabaseId")
                               or row.get("databaseId"), "startedAt": row.get("executionStartDate"),
                               "status": status, "executionArn": arn,
                               "triggeredBy": row.get("triggeredByUserId")})
                if len(active) >= limit:
                    break
            if "LastEvaluatedKey" not in resp or len(active) >= limit:
                break
            kw["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    except Exception as exc:  # noqa: BLE001
        if permissions.is_denied(exc):
            raise PermissionDenied(f"cannot list workflow executions of {env}",
                                   permissions.classify_exception(exc, "states", table), env=env, role=role) from exc
        raise
    return {"table": table, "scanned": scanned, "active": active}


def abort_workflow_executions(ctx, run, env: str, active: list[dict], role: str = "deploy") -> list[dict]:
    """StopExecution for each listed execution (only with --abort-workflow-executions). VAMS marks the record
    ABORTED when its execution service next syncs with Step Functions."""
    sfn = ctx.aws.client(env, "stepfunctions", role)
    out = []
    for a in active:
        if not a.get("executionArn"):
            out.append({**a, "aborted": False, "reason": "no Step Functions execution ARN recorded"})
            continue
        try:
            sfn.stop_execution(executionArn=a["executionArn"], error="VraMaintenance",
                               cause=f"aborted by vra run {run.run_id} ({ctx.initiator})"[:32768])
            out.append({**a, "aborted": True})
        except Exception as exc:  # noqa: BLE001
            if permissions.is_denied(exc):
                raise PermissionDenied(f"states:StopExecution denied on {env}",
                                       permissions.classify_exception(exc, "states", a["executionArn"]),
                                       env=env, role=role) from exc
            out.append({**a, "aborted": False, "reason": str(exc)[:300]})
    return out


def workflow_executions_step(ctx, run, env: str, step_id: str, purpose: str, abort: bool = False,
                             role: str = "deploy", names: dict | None = None) -> dict:
    """List (and only with ``abort``: stop) running workflow executions before ``purpose``; report them."""
    def _step(sc):
        if ctx.dry_run:
            try:
                res = active_workflow_executions(ctx, env, role, names)
            except PermissionDenied:
                raise
            except Exception as exc:  # noqa: BLE001 - a dry run reports what it could not read
                return {"dryRun": True, "unavailable": str(exc)[:300], "active": []}
        else:
            res = active_workflow_executions(ctx, env, role, names)
        act = res.get("active") or []
        if act:
            msg = (f"{len(act)} VAMS workflow execution(s) still running on {env} before the {purpose}: "
                   + ", ".join(f"{a.get('workflowId')}/{a.get('executionId')}" for a in act[:10]))
            if abort and not ctx.dry_run:
                res["aborted"] = abort_workflow_executions(ctx, run, env, act, role)
                msg += f"; aborted {sum(1 for a in res['aborted'] if a.get('aborted'))} (--abort-workflow-executions)"
            else:
                msg += ("; they keep running and may write while data changes (pass --abort-workflow-executions "
                        "to stop them)")
            run.state.setdefault("notes", []).append(msg)
            run.save()
            sc.log(msg)
        return res
    return run.step(step_id, _step, f"active workflow executions ({purpose})", revalidate=True)
