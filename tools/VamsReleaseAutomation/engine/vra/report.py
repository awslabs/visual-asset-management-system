# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Run reports (Markdown + JSON) with DORA-style env metrics, and notifications
(sns | email (SES) | slack-webhook | teams-webhook). Notification targets are Secrets Manager secret
NAMES in vra.yaml; the secret value (topic ARN / address / webhook URL) is never logged.
"""

from __future__ import annotations

import datetime as dt
import json
import time
import urllib.request
from typing import Any

from vra import tests_runner, util

ICON = {"done": "ok", "skipped": "skip", "failed": "FAIL", "blocked": "BLOCKED", "running": "...", "pending": "-"}


def _parse(ts: str | None):
    if not ts:
        return None
    try:
        return dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def env_metrics(ctx, env: str, days: int = 30) -> dict:
    hist = ctx.ledger.history(env)
    now = ctx.now()
    since = now - dt.timedelta(days=days)
    deploys = [h for h in hist if h.get("action") == "deploy" and (_parse(h.get("ts")) or now) >= since]
    ok = [h for h in deploys if h.get("result") == "SUCCEEDED"]
    failed = [h for h in deploys if h.get("result") != "SUCCEEDED"]
    # time to restore: failed deploy -> next successful deploy/rollback
    restores = []
    for f in failed:
        tf = _parse(f.get("ts"))
        nxt = next((h for h in hist if h.get("action") in ("deploy", "rollback") and h.get("result") == "SUCCEEDED"
                    and (_parse(h.get("ts")) or tf) > tf), None) if tf else None
        if nxt:
            restores.append(((_parse(nxt["ts"]) - tf).total_seconds()) / 60)
    current = ctx.ledger.current_deploy(env)
    return {"env": env, "windowDays": days, "deploys": len(deploys), "succeeded": len(ok), "failed": len(failed),
            "changeFailureRate": round(len(failed) / len(deploys), 3) if deploys else None,
            "meanMinutesToRestore": round(sum(restores) / len(restores), 1) if restores else None,
            "current": {k: (current or {}).get(k) for k in ("build", "vamsVersion", "ts", "slot")}}


def build_report(ctx, run) -> dict:
    st = run.state
    steps = st["steps"]
    junit = tests_runner.aggregate_junit(run.junit_dir)
    names = ctx.ledger.env_names() if ctx.ledger.exists else []
    found = {st["env"]} if st.get("env") in names else set()
    for s in steps:
        parts = s["id"].split(".")
        for i, part in enumerate(parts[:-1]):
            if part in ("deploy", "test", "backup", "restore") and parts[i + 1] in names:
                found.add(parts[i + 1])
    envs = sorted(found)
    rep = {"runId": st["runId"], "workflow": st["workflow"], "status": st["status"], "message": st.get("message"),
           "initiator": st.get("initiator"), "startedAt": st["startedAt"], "endedAt": st.get("endedAt"),
           "args": st.get("args"), "build": st.get("build"), "checkpoint": st.get("checkpoint"),
           "steps": [{k: s.get(k) for k in ("id", "name", "status", "startedAt", "endedAt", "detail", "attempts")}
                     for s in steps],
           "itRequests": st.get("itRequests", []), "approvals": st.get("approvals", []),
           "takeovers": st.get("takeovers", []), "humanAuthorizations": st.get("humanAuthorizations", []),
           "conflicts": util.read_json(run.dir / "conflicts.json") if (run.dir / "conflicts.json").exists() else None,
           "tests": junit, "notes": st.get("notes", []), "result": st.get("result"),
           "metrics": [env_metrics(ctx, e) for e in envs], "writeFreeze": st.get("writeFreeze") or {},
           "workflowExecutions": {s["id"]: s.get("result") for s in steps
                                  if s["id"].endswith("workflow-executions") and s.get("result")}}
    return rep


def _data_copy_section(copy: dict) -> list[str]:
    """Counts of a data copy (or of its --dry-run plan) for the W10 report."""
    tables = {k: v for k, v in (copy.get("tables") or {}).items() if isinstance(v, dict) and not v.get("skipped")}
    skipped = sorted(k for k, v in (copy.get("tables") or {}).items() if isinstance(v, dict) and v.get("skipped"))
    if not tables and not copy.get("buckets"):
        return []
    dry = bool(copy.get("dryRun"))
    L = ["", "## Data copy (dry-run plan)" if dry else "## Data copy", ""]
    if dry:
        L.append(f"- Tables: {len(tables)}; source items {sum(v.get('sourceItems') or 0 for v in tables.values())} "
                 f"would replace {sum(v.get('targetItems') or 0 for v in tables.values())} target items "
                 f"(DynamoDB ItemCount, approximate)")
    else:
        L.append(f"- Tables: {len(tables)}; items cleared {sum(v.get('cleared') or 0 for v in tables.values())}, "
                 f"written {sum(v.get('written') or 0 for v in tables.values())}")
    if skipped:
        L.append(f"- Not copied: {', '.join(f'`{k}`' for k in skipped)}")
    L += ["", "| Source bucket | Target bucket | " + ("Source objects | Target objects |" if dry else "Copied | Removed |"),
          "|---|---|---|---|"]
    for b in copy.get("buckets") or []:
        if not isinstance(b, dict):
            continue
        a, c = (b.get("sourceObjects"), b.get("targetObjects")) if dry else (b.get("copied", b.get("objects")),
                                                                              b.get("removed"))
        L.append(f"| `{b.get('source')}` | `{b.get('target')}` | {a if a is not None else b.get('skipped', '-')} | "
                 f"{c if c is not None else '-'} |")
    return L


def render_markdown(rep: dict) -> str:
    L = [f"# vra {rep['workflow']} run `{rep['runId']}`: **{rep['status']}**", ""]
    if rep.get("message"):
        L += [f"> {rep['message']}", ""]
    L += [f"- Initiator: `{rep.get('initiator')}`", f"- Started: {rep['startedAt']}  Ended: {rep.get('endedAt') or '-'}"]
    if rep.get("build"):
        L.append(f"- Build: `{rep['build']}`")
    if rep.get("checkpoint"):
        L.append(f"- Checkpoint: `{rep['checkpoint']}`")
    L += ["", "## Steps", "", "| | Step | Detail |", "|---|---|---|"]
    for s in rep["steps"]:
        L.append(f"| {ICON.get(s['status'], s['status'])} | `{s['id']}` | {(s.get('detail') or '').replace('|', '/')[:200]} |")
    t = rep["tests"]
    if t["tests"]:
        L += ["", "## Tests", "", f"{t['passed']} passed, {t['failures']} failed, {t['errors']} errors, "
              f"{t['skipped']} skipped ({len(t['files'])} JUnit files)."]
        for f in t["failed"][:20]:
            L.append(f"- FAIL `{f}`")
    if rep["workflow"] == "data-copy":
        L += _data_copy_section((rep.get("result") or {}).get("copy") or {})
    if rep["itRequests"]:
        L += ["", "## Waiting on IT", ""]
        for r in rep["itRequests"]:
            pending = " pending, did not block this run;" if r.get("blocking") is False else ""
            L.append(f"- {r['id']} ({r['status']}):{pending} step `{r['step']}`, {len(r.get('actions', []))} action(s)"
                     f" - `{r.get('path')}`")
    if rep.get("conflicts") and rep["conflicts"].get("unresolved"):
        L += ["", "## Merge conflicts needing resolution", ""]
        for c in rep["conflicts"]["unresolved"]:
            L.append(f"- `{c['path']}` ({c.get('reason')})")
    if rep["approvals"]:
        L += ["", "## Approvals", ""]
        for a in rep["approvals"]:
            L.append(f"- {a.get('env')}: `{a.get('build')}` approved by `{a.get('approver')}` at {a.get('approvedAt')}"
                     f" (ticket {a.get('ticket') or '-'})")
    if rep.get("takeovers") or rep.get("humanAuthorizations"):
        L += ["", "## Takeovers and human-only actions", ""]
        for t in rep.get("takeovers") or []:
            prev = t.get("previous") or {}
            L.append(f"- takeover of `{t.get('lock')}` by `{t.get('by')}` at {t.get('at')} from host "
                     f"`{prev.get('fromHost') or '-'}`" + (f" (dirty: {prev['dirty']})" if prev.get("dirty") else ""))
        for h in rep.get("humanAuthorizations") or []:
            via = f"approval `{h.get('approval')}` by `{h.get('approver')}`" if h.get("how") == "approval" else \
                "confirmed at a terminal"
            L.append(f"- `{h.get('action')}` by `{h.get('by')}`: {via}")
    if rep.get("writeFreeze"):
        L += ["", "## Write freeze", ""]
        for env, wf in rep["writeFreeze"].items():
            e, r = wf.get("engaged") or {}, wf.get("released") or {}
            who = f", acknowledged by `{e['acknowledgedBy']}` at {e.get('acknowledgedAt')} ({e.get('source')})" \
                if e.get("acknowledgedBy") else ""
            where = f" (`{e['parameter']}`)" if e.get("parameter") else ""
            lifted = "lifted" if r.get("released") else ("not lifted: " + str(r.get("error") or r.get("reason"))
                                                         if r else "still engaged")
            L.append(f"- {env}: {e.get('backend')}{where}{who}; {lifted}"
                     f"{' (after a failure)' if r.get('onFailure') else ''}")
    for sid, res in (rep.get("workflowExecutions") or {}).items():
        act = res.get("active") or []
        if act:
            L += ["", f"## Running workflow executions (`{sid}`)", ""]
            aborted = {a.get("executionId") for a in res.get("aborted") or [] if a.get("aborted")}
            L += [f"- `{a.get('workflowId')}` / `{a.get('executionId')}` started {a.get('startedAt') or '-'}: "
                  f"{'aborted' if a.get('executionId') in aborted else a.get('status')}" for a in act[:50]]
    if rep["metrics"]:
        L += ["", "## Environment metrics (30 days)", "", "| Env | Current build | VAMS | Deploys | Failed | CFR | MTTR (min) |",
              "|---|---|---|---|---|---|---|"]
        for m in rep["metrics"]:
            c = m["current"]
            L.append(f"| {m['env']} | `{c.get('build') or '-'}` | {c.get('vamsVersion') or '-'} | {m['deploys']} | "
                     f"{m['failed']} | {m['changeFailureRate'] if m['changeFailureRate'] is not None else '-'} | "
                     f"{m['meanMinutesToRestore'] if m['meanMinutesToRestore'] is not None else '-'} |")
    if rep["notes"]:
        L += ["", "## Notes", ""] + [f"- {n}" for n in rep["notes"]]
    L += ["", f"Resume with `vra resume {rep['runId']}`." if rep["status"] in ("BLOCKED_ON_IT", "NEEDS_HUMAN") else ""]
    return "\n".join(L).rstrip() + "\n"


def write(ctx, run) -> dict:
    rep = build_report(ctx, run)
    util.write_json(run.dir / "report.json", rep)
    util.atomic_write(run.dir / "report.md", render_markdown(rep))
    if ctx.store is not None and not ctx.dry_run:
        for f in ("report.json", "report.md"):
            ctx.store.push_file(run.dir / f)
    return rep


def summary_line(rep: dict) -> str:
    return f"vra {rep['workflow']} {rep['runId']}: {rep['status']}" + (f" - {rep['message']}" if rep.get("message") else "")


def notify(ctx, run) -> dict:
    cfg = ctx.ledger.project.get("notifications") or {"type": "none"}
    typ = cfg.get("type", "none")
    rep = util.read_json(run.dir / "report.json") if (run.dir / "report.json").exists() else write(ctx, run)
    if typ == "none":
        return {"sent": False, "reason": "notifications.type=none"}
    if cfg.get("onlyOn") and rep["status"] not in cfg["onlyOn"]:
        return {"sent": False, "reason": f"status {rep['status']} not in onlyOn"}
    subject = summary_line(rep)[:100]
    body = (run.dir / "report.md").read_text(encoding="utf-8")
    payload_preview = {"type": typ, "subject": subject, "bytes": len(body)}
    if ctx.dry_run:
        return {"sent": False, "dryRun": True, **payload_preview}
    sm = ctx.aws.tools_client("secretsmanager", cfg.get("region"))
    target = sm.get_secret_value(SecretId=cfg["target"])["SecretString"].strip()
    if typ == "sns":
        ctx.aws.tools_client("sns", cfg.get("region")).publish(TopicArn=target, Subject=subject, Message=body[:250000])
    elif typ == "email":
        ctx.aws.tools_client("ses", cfg.get("region")).send_email(
            Source=cfg["from"], Destination={"ToAddresses": [a.strip() for a in target.split(",") if a.strip()]},
            Message={"Subject": {"Data": subject}, "Body": {"Text": {"Data": body}}})
    elif typ == "slack-webhook":
        _post_json(target, {"text": f"*{subject}*\n```{body[:3500]}```"})
    elif typ == "teams-webhook":
        _post_json(target, {"@type": "MessageCard", "@context": "https://schema.org/extensions", "summary": subject,
                            "title": subject, "text": body[:20000].replace("\n", "  \n")})
    return {"sent": True, **payload_preview}


def _post_json(url: str, payload: dict) -> None:
    if not url.startswith("https://"):
        raise ValueError("webhook URL must be https")
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 - https enforced above
        resp.read()


def status(ctx, run_id: str | None) -> Any:
    from vra.state import Run
    if run_id:
        run = Run.load(ctx.state_dir, run_id, store=ctx.store, home=ctx.home)
        return {k: run.state.get(k) for k in ("runId", "workflow", "status", "message", "env", "build", "startedAt",
                                              "endedAt", "itRequests", "checkpoint", "host", "rev", "takeovers")} | {
            "steps": [{k: s.get(k) for k in ("id", "status", "detail")} for s in run.state["steps"]]}
    return {"runs": _runs(ctx)[-50:]}


def _runs(ctx) -> list[dict]:
    runs = []
    d = ctx.runs_dir
    if d.exists():
        for p in sorted(d.iterdir()):
            sp = p / "state.json"
            if sp.exists():
                try:
                    s = util.read_json(sp)
                except ValueError:
                    runs.append({"runId": p.name, "status": "UNREADABLE"})
                    continue
                runs.append({k: s.get(k) for k in ("runId", "workflow", "status", "env", "build", "startedAt",
                                                   "host", "initiator", "dryRun")})
    if ctx.store is not None:
        seen = {r["runId"] for r in runs}
        for key in ctx.store.list_keys("runs/"):
            if key.endswith("/state.json"):
                rid = key.split("/")[-2]
                if rid not in seen:
                    got = ctx.store.get_run_state(rid)
                    if got:
                        s = got[0]
                        runs.append({k: s.get(k) for k in ("runId", "workflow", "status", "env", "build",
                                                           "startedAt", "host", "initiator", "dryRun")})
    return sorted(runs, key=lambda r: r.get("startedAt") or "")


def status_all(ctx) -> dict:
    """Every run and lock in the shared state (all machines), and the home's hosts."""
    ctx.home.pull()
    lock = ctx.backend.lock
    locks = []
    now = time.time()
    keys = [h.get("key") for h in lock.list()] if hasattr(lock, "list") else []
    if not keys:  # DynamoDB: no table scan; ask per env and per running run
        keys = list(ctx.ledger.env_names()) + [f"run-{r['runId']}" for r in _runs(ctx) if r.get("status") == "RUNNING"]
    for key in keys:
        h = lock.holder(key)
        if not h:
            continue
        exp = float(h.get("expiresAt") or 0)
        locks.append({"key": key, "runId": h.get("runId"), "host": h.get("host"), "heartbeatAt": h.get("heartbeatAt"),
                      "expiresAt": util.iso_from_epoch(exp), "live": exp > now, "dirty": h.get("dirty"),
                      "held": bool(h.get("held")), "takeovers": len(h.get("takeovers") or [])})
    runs = _runs(ctx)
    orphans = orphaned_runs(ctx, runs)
    gone = {o["runId"] for o in orphans}
    active = [{**r, **({"orphaned": True} if r.get("runId") in gone else {})}
              for r in runs if r.get("status") in ("RUNNING", "BLOCKED_ON_IT", "NEEDS_HUMAN")]
    return {"home": ctx.home.describe(), "backend": ctx.backend.kind, "locks": locks, "activeRuns": active,
            "orphanedRuns": orphans, "runs": runs[-100:]}


# A dry run takes no run lease, so its liveness is unknown: it is only called orphaned this long after it started.
DRY_RUN_ORPHAN_SECONDS = 24 * 3600


def _epoch(iso: str | None) -> float | None:
    try:
        return dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def orphaned_runs(ctx, runs: list[dict] | None = None) -> list[dict]:
    """RUNNING runs that nothing owns any more: their run lease (``run-<runId>``) expired or is gone (the process
    was killed, the machine recycled, or a 0.4.0 run whose first publish was refused). Reported, never changed:
    only a person decides whether to take the run over."""
    from vra.state import lock_key_for_run
    runs = _runs(ctx) if runs is None else runs
    lock = ctx.backend.lock
    now = time.time()
    out = []
    for r in runs:
        if r.get("status") != "RUNNING" or not r.get("runId"):
            continue
        h = lock.holder(lock_key_for_run(r["runId"]))
        exp = float((h or {}).get("expiresAt") or 0)
        if h and not h.get("corrupt") and exp > now:
            continue
        if h is None and r.get("dryRun"):
            started = _epoch(r.get("startedAt"))
            if started is None or now - started < DRY_RUN_ORPHAN_SECONDS:
                continue
        lease = f"expired at {util.iso_from_epoch(exp)}" if h and exp else "none held"
        out.append({"runId": r["runId"], "workflow": r.get("workflow"), "env": r.get("env"), "host": r.get("host"),
                    "startedAt": r.get("startedAt"), "lease": lease,
                    "fix": f"vra resume {r['runId']} --takeover",
                    "message": f"run {r['runId']} is RUNNING but its run lease is {lease}: nothing owns it. Check "
                               f"that nothing of it still runs on {r.get('host') or 'its host'}, then a person takes "
                               f"it over with `vra resume {r['runId']} --takeover` (or leaves it; vra deletes "
                               f"nothing)"})
    return out


def history_verify(ctx, env: str | None = None) -> dict:
    """Check the env history: every record parses, carries its own id, sequence numbers are unique and match the
    file names, nothing sits only on this machine (unpushed under ``sync.type: git``), and no 0.3 ledger
    ``history.jsonl`` is lying around as if it were evidence."""
    from vra.ledger import record_id
    ctx.home.pull()
    envs = [env] if env else ctx.ledger.env_names()
    problems: list[str] = []
    warnings: list[str] = []
    counts: dict[str, int] = {}
    for e in envs:
        legacy = ctx.ledger.root / "envs" / e / "history.jsonl"
        if legacy.exists() and legacy.stat().st_size:
            problems.append(f"{e}: {legacy.relative_to(ctx.ledger.root)} holds history outside the state backend; it "
                            f"is not evidence. Run `vra home migrate` (or move the records) and delete the file")
        hist = ctx.backend.history
        if hasattr(hist, "files"):
            files = hist.files(e)
            seqs: dict[int, str] = {}
            for f in files:
                head = f.name.split("-", 1)[0]
                try:
                    rec = util.read_json(f)
                except ValueError:
                    problems.append(f"{e}: {f.name} is not valid JSON")
                    continue
                if not head.isdigit() or int(head) != int(rec.get("seq") or -1):
                    problems.append(f"{e}: {f.name} has seq {rec.get('seq')!r}, its file name says {head!r}")
                elif int(head) in seqs:
                    problems.append(f"{e}: sequence {int(head)} used twice ({seqs[int(head)]}, {f.name})")
                else:
                    seqs[int(head)] = f.name
                body = {k: v for k, v in rec.items() if k not in ("seq", "ledger")}
                if rec.get("id") and rec.get("id") != record_id(body) and not rec.get("migratedFrom"):
                    problems.append(f"{e}: {f.name} was edited after it was written (id does not match content)")
                tracked = ctx.home.sync.tracked(f)
                if tracked is False:
                    problems.append(f"{e}: {f.name} exists only on this machine (not committed and pushed); "
                                    f"run `vra home sync`")
            if seqs and sorted(seqs) != list(range(1, max(seqs) + 1)):
                missing = sorted(set(range(1, max(seqs) + 1)) - set(seqs))
                warnings.append(f"{e}: sequence gaps {missing[:10]} (a record was removed, or never synced)")
            counts[e] = len(files)
        else:
            counts[e] = len(hist.read(e))
    # not a history defect (the exit code is unchanged), but a run nothing owns any more is worth a person's look
    orphans = orphaned_runs(ctx)
    warnings += [f"orphaned run: {o['message']}" for o in orphans]
    return {"ok": not problems, "problems": problems, "warnings": warnings, "records": counts,
            "orphanedRuns": orphans, "backend": ctx.backend.kind, "sync": ctx.home.sync.type}
