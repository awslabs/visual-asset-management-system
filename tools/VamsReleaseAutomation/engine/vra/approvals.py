# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Approval gates and separation of duties.

An approval record lives at ``ledger/approvals/<env>/<id>.yaml`` (or is written by a CI job from the
platform's own approval: CodePipeline manual approval, GitHub environment reviewers, GitLab deployment
approvals). ``--approval A`` names the record id (default: the build id) or a path. The record's ``build``
field must equal the gate subject:

- deploy / promote / rollback : the build id
- restore                      : ``restore:<checkpointId>``
- data-copy                    : ``data-copy:<sourceEnv>`` (target env) and ``data-copy-to:<targetEnv>``
                                 (source env, from its ``dataOwners``)

Checks: schema-valid; env and subject match; approver listed in the env's ``approvers`` (an
assumed-role ARN only matches an ARN or ARN glob entry, never a bare session name, because session names
are chosen by the caller); approver differs from every known name of the run initiator (SoD, see
vra.identity); ``scope`` present and covering the action; not expired (``expiresAt``, default
``approvedAt`` + ``approvalMaxAgeHours`` (72)); bound to this CI run when the run is in CI (``source``);
not already consumed by another run (history ``approvalSha256``).
"""

from __future__ import annotations

import datetime as dt
import fnmatch
import re
import shutil
from pathlib import Path

import jsonschema

from vra import cron, ledger as ledger_mod, util
from vra.errors import NeedsHuman, UsageError

DEFAULT_MAX_AGE_HOURS = 72
_ARN_RE = re.compile(r"^arn:[a-z0-9-]+:(sts|iam)::")


def normalize_identity(ident: str | None) -> str:
    """Comparable identity: lower-case; STS assumed-role ARNs reduce to their session name;
    email-ish ids keep their local part + domain; ``user@REALM`` style kept as is."""
    if not ident:
        return ""
    s = ident.strip().lower()
    m = re.match(r"^arn:[a-z0-9-]+:sts::\d{12}:assumed-role/[^/]+/(.+)$", s)
    if m:
        return m.group(1)
    m = re.match(r"^arn:[a-z0-9-]+:iam::\d{12}:user/(?:.+/)?([^/]+)$", s)
    if m:
        return m.group(1)
    return s


def same_person(a: str | None, b: str | None) -> bool:
    """Liberal on purpose (it is a DENY check): any spelling that could be the same person counts."""
    na, nb = normalize_identity(a), normalize_identity(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    # alice@example.com vs alice
    return na.split("@")[0] == nb.split("@")[0] and ("@" in na) != ("@" in nb)


def approver_listed(approver: str, approvers: list[str]) -> bool:
    """Strict on purpose (it is an ALLOW check).

    - An ARN approver (CodePipeline ``updatedBy``) matches only ARN entries or ARN globs, compared on the full
      principal (account, role, session): ``arn:*:sts::111111111111:assumed-role/ReleaseApprover*/alice@example.com``.
    - A plain id (GitHub / GitLab login, email in a hand-written record) matches an equal plain entry.
    """
    a = (approver or "").strip().lower()
    if not a:
        return False
    for entry in approvers or []:
        e = entry.strip().lower()
        if _ARN_RE.match(a):
            if e.startswith("arn:") and fnmatch.fnmatchcase(a, e):
                return True
        elif not e.startswith("arn:") and a == e:
            return True
    return False


def load_record(ctx, env: str, approval: str | None, subject: str) -> tuple[dict, Path]:
    ref = approval or subject
    p = Path(ref)
    if p.suffix in (".yaml", ".yml") and not p.is_absolute() and not p.exists():
        p = ctx.ledger.root / p  # e.g. --approval approvals/test/<id>.yaml, relative to the ledger
    if not (p.suffix in (".yaml", ".yml") and p.exists()):
        safe = re.sub(r"[^A-Za-z0-9._:-]+", "_", ref).replace(":", "_")
        p = ctx.ledger.approval_path(env, safe)
    if not p.exists():
        raise NeedsHuman(f"approval required for {env} ({subject}); no record at {p}",
                         {"expected": str(p), "subject": subject})
    rec = util.read_yaml(p) or {}
    errs = list(jsonschema.Draft202012Validator(ledger_mod.schema("approval")).iter_errors(rec))
    if errs:
        raise NeedsHuman(f"approval record {p} is invalid: {errs[0].message}")
    return rec, p


def record_digest(rec: dict) -> bytes:
    """SHA-256 of the record's canonical JSON without ``signature`` (what a signing CI approval step signs)."""
    import hashlib
    import json
    body = {k: v for k, v in rec.items() if k != "signature"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode()).digest()


def verify_record_signature(ctx, rec: dict, key_arn: str) -> list[str]:
    sig = rec.get("signature") or {}
    if not sig:
        return [f"the env requires approval records signed by {key_arn}; this record carries no signature"]
    if sig.get("kmsKeyArn") != key_arn:
        return [f"record is signed by {sig.get('kmsKeyArn')!r}, the env requires {key_arn!r}"]
    import base64
    try:
        region = key_arn.split(":")[3] or None
        ok = ctx.aws.tools_client("kms", region).verify(
            KeyId=key_arn, Message=record_digest(rec), MessageType="DIGEST",
            Signature=base64.b64decode(sig["value"]),
            SigningAlgorithm=sig.get("algorithm") or "ECDSA_SHA_256").get("SignatureValid")
    except Exception as exc:  # noqa: BLE001 - an unverifiable signature is a rejected one
        return [f"approval signature could not be verified ({type(exc).__name__})"]
    return [] if ok else ["approval signature is not valid"]


def _ts(value) -> dt.datetime:
    t = value if isinstance(value, dt.datetime) else dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def check(ctx, env: str, subject: str, action: str, approval: str | None = None, initiator: str | None = None,
          force_required: bool = False, approvers: list[str] | None = None) -> dict | None:
    """Return the approval record (with path) or None when the env needs no approval. Raises NeedsHuman.

    ``approvers`` overrides the env's ``approvers`` (data-copy source side uses ``dataOwners``). An approval that
    is passed explicitly is always verified and recorded, also on an env that does not require one: an offered
    approval is never silently ignored.
    """
    from vra import identity
    e = ctx.ledger.env(env)
    if not (e.get("requiresApproval") or force_required or approval):
        return None
    # a gated action runs on the reviewed, pushed ledger only (shared home synced by git: clean + HEAD == remote)
    gate = ctx.home.sync.gate_check() if e.get("requiresApproval") else {"sync": ctx.home.sync.type}
    ident = identity.verify(ctx)
    if ident.run_ref and e.get("requiresApproval") and not approval and not initiator:
        raise NeedsHuman(f"{env} is approval-gated and this is a CI run ({ident.source}): pass the record the CI "
                         f"approval step wrote with --approval (files found in the ledger are not bound to this run)")
    rec, path = load_record(ctx, env, approval, subject)
    names = [initiator] if initiator else ident.all_names()
    problems = []
    if ident.source == "generic" and e.get("requiresApproval") and not e.get("allowGenericCi"):
        problems.append(f"this run is driven by VRA_CI_PROVIDER=generic, whose initiator is asserted by that CI; "
                        f"envs/{env} must opt in with allowGenericCi: true")
    pinned = ctx.ledger.project.get("engineVersion")
    if pinned and e.get("requiresApproval"):
        from vra import __version__
        if str(pinned) != __version__:
            problems.append(f"the ledger pins engineVersion {pinned}; this is vra {__version__}. Gated actions run "
                            f"only with the pinned engine")
    if e.get("approvalSigningKeyArn"):
        problems += verify_record_signature(ctx, rec, e["approvalSigningKeyArn"])
    if e.get("requiresApproval") and ctx.home.sync.tracked(path) is False:
        problems.append(f"approval record {path.name} is not committed in the shared home; a gated action is only "
                        f"authorized by a reviewed, pushed record")
    if e.get("requiresApproval") and not initiator:
        authors = [a for a in (getattr(ctx, "change_authors", None) or []) if a]
        for a in authors:
            if same_person(rec.get("approver"), a):
                problems.append(f"separation of duties: approver {rec['approver']!r} authored or committed the "
                                f"change in this build ({a!r})")
                break
    if rec.get("env") != env:
        problems.append(f"record env {rec.get('env')!r} != {env!r}")
    if str(rec.get("build")) != subject:
        problems.append(f"record is for {rec.get('build')!r}, not {subject!r}")
    allowed = approvers if approvers is not None else (e.get("approvers") or [])
    if not approver_listed(rec["approver"], allowed):
        problems.append(f"approver {rec['approver']!r} is not in envs/{env} "
                        f"{'dataOwners' if approvers is not None else 'approvers'}")
    for n in names:
        if same_person(rec["approver"], n):
            problems.append(f"separation of duties: approver {rec['approver']!r} is the run initiator {n!r}")
            break
    scope = rec.get("scope")
    if not scope:
        problems.append("approval has no scope; state what it covers (deploy, promote, rollback, restore, "
                        "data-copy, emergency, allow-replacements)")
    elif action not in scope:
        problems.append(f"approval scope {scope} does not cover {action!r}")
    try:
        if rec.get("expiresAt"):
            exp = _ts(rec["expiresAt"])
        else:
            hours = int(e.get("approvalMaxAgeHours") or DEFAULT_MAX_AGE_HOURS)
            exp = _ts(rec["approvedAt"]) + dt.timedelta(hours=hours)
        if exp < ctx.now():
            problems.append(f"approval expired at {exp.isoformat()}")
        if _ts(rec["approvedAt"]) > ctx.now() + dt.timedelta(minutes=5):
            problems.append(f"approval is dated {rec['approvedAt']}, in the future; an approval cannot predate "
                            f"its own decision")
    except ValueError:
        problems.append("approvedAt / expiresAt is not ISO-8601")
    if ident.run_ref and not initiator:
        if rec.get("source") != ident.run_ref:
            problems.append(f"approval is bound to {rec.get('source')!r}, this run is {ident.run_ref!r} "
                            f"(an approval given for another pipeline run cannot authorize this one)")
    sha = util.sha256_file(path)
    consumed = [h for h in ctx.ledger.history(env) if h.get("approvalSha256") == sha
                and h.get("runId") not in (None, ctx.run_id)]
    claimed = ctx.backend.consumed.get(env, sha)
    if claimed and claimed.get("runId") not in (None, ctx.run_id) and \
            _claim_binding(ctx.home, ctx.store, ctx.ledger, env, sha, claimed):
        consumed.append({"runId": claimed.get("runId"), "action": claimed.get("action")})
    if consumed:
        problems.append(f"approval was already used by run {consumed[-1].get('runId')} "
                        f"({consumed[-1].get('action')}); each approval authorizes one run")
    if problems:
        raise NeedsHuman(f"approval {path.name} for {env} rejected: " + "; ".join(problems),
                         {"approval": str(path), "problems": problems})
    return {**rec, "path": str(path), "sha256": sha, "verifiedAt": util.now_iso(), "initiator": names[0],
            "action": action, "env": env, "ledgerGate": gate, "ledger": ctx.ledger_ref()}


def check_change_window(ctx, env: str, emergency: bool, subject: str, approval: str | None) -> dict:
    e = ctx.ledger.env(env)
    win = e.get("changeWindow")
    if not win:
        return {"window": None, "inWindow": True}
    if cron.in_window(win, ctx.now()):
        return {"window": win, "inWindow": True}
    if not emergency:
        raise NeedsHuman(f"{env} is outside its change window {win}; re-run with --emergency and an approval")
    rec = check(ctx, env, subject, "emergency", approval, force_required=True)
    return {"window": win, "inWindow": False, "emergency": True, "approval": rec and rec.get("path")}


def claim(run, rec: dict) -> dict:
    """Mark the approval record as used by this run (write-once ``approvals-consumed/<env>/<sha256>``). Another run
    that already claimed it wins; this one is refused. A resumed run re-claims its own record."""
    backend = getattr(run, "backend", None)
    if backend is None or run.dry_run or not rec.get("sha256"):
        return {}
    import socket
    mine = {"runId": run.run_id, "env": rec.get("env"), "action": rec.get("action"), "subject": rec.get("build"),
            "approver": rec.get("approver"), "approval": rec.get("path"), "at": util.now_iso(),
            "host": socket.gethostname()}
    got = backend.consumed.claim(rec.get("env"), rec["sha256"], mine)
    if got.get("runId") != run.run_id and not _claim_binding(run.home, run.store, None, rec.get("env"),
                                                             rec["sha256"], got, history=backend.history):
        # the earlier run neither runs nor recorded a change with this approval (it stopped at a later gate):
        # this run takes the claim over
        got = backend.consumed.replace(rec.get("env"), rec["sha256"], {**mine, "supersedes": got.get("runId")})
    if got.get("runId") != run.run_id:
        raise NeedsHuman(f"approval {Path(str(rec.get('path'))).name} was already used by run {got.get('runId')} "
                         f"({got.get('action')}, host {got.get('host')}); each approval authorizes one run",
                         {"consumed": got})
    return got


def _claim_binding(home, store, ledger, env: str, sha: str, claim: dict, history=None) -> bool:
    """Whether another run's claim still holds: that run is live (RUNNING), or it recorded a change with this
    approval in the env history. A run that stopped at a later gate does not burn the approval."""
    rid = claim.get("runId")
    if not rid or str(rid).startswith("human:"):
        return True
    status = None
    try:
        if store is not None:
            got = store.get_run_state(rid)
            status = got[0].get("status") if got else None
        if status is None:
            p = home.runs_dir / str(rid) / "state.json"
            status = util.read_json(p).get("status") if p.exists() else None
    except Exception:  # noqa: BLE001 - unknown state: the claim holds
        return True
    if status == "RUNNING":
        return True
    recs = (ledger.history(env) if ledger is not None else (history.read(env) if history is not None else []))
    return any(h.get("approvalSha256") == sha and h.get("runId") == rid for h in recs)


def record(run, rec: dict | None) -> None:
    """Record a verified approval in the run (claiming it, so no other run can use it), and keep a copy of the
    record in the run directory, so the evidence outlives a CI job's workspace."""
    if not rec:
        return
    claim(run, rec)
    entry = {k: rec.get(k) for k in ("build", "env", "approver", "approvedAt", "ticket", "path", "sha256",
                                     "source", "scope", "verifiedAt", "initiator", "action", "ledger")}
    apps = run.state.setdefault("approvals", [])
    if any(a.get("sha256") == entry["sha256"] and a.get("action") == entry["action"] for a in apps):
        return  # a revalidated gate on resume
    try:
        dest = run.dir / "approvals" / f"{rec['env']}-{Path(rec['path']).name}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(rec["path"], dest)
        entry["copy"] = str(dest)
        if run.store is not None and not run.dry_run:
            run.store.push_file(dest)
    except OSError:
        pass
    apps.append(entry)
    run.save()


def history_fields(rec: dict | None) -> dict:
    """Fields every gated history record carries, so a consumed approval cannot be reused."""
    if not rec:
        return {}
    return {"approval": rec.get("path"), "approvalSha256": rec.get("sha256"), "approver": rec.get("approver"),
            "approvalSource": rec.get("source"), "approvalLedger": rec.get("ledger")}


def write_template(ctx, env: str, subject: str) -> Path:
    """Helper for humans/CI: write an unfilled approval record skeleton (does not approve anything)."""
    p = ctx.ledger.approval_path(env, re.sub(r"[^A-Za-z0-9._-]+", "_", subject))
    if p.exists():
        raise UsageError(f"{p} exists")
    util.write_yaml(p, {"build": subject, "env": env, "approver": "<approver id>", "approvedAt": "<ISO-8601>",
                        "expiresAt": None, "scope": ["<deploy|promote|rollback|restore|data-copy|emergency>"],
                        "ticket": None, "notes": None})
    return p
